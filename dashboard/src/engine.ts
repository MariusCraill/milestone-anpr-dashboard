import { EventEmitter } from 'node:events';
import type { DB } from './db.ts';
import type { Camera, CameraRole, PlateRead, ReadResult } from './types.ts';

export interface EngineOptions {
  debounceMs: number;
  reentryGraceMs: number;
  minConfidence: number;
}

export const ROLES: CameraRole[] = ['entry', 'exit', 'area', 'unassigned', 'ignore'];

export function normalisePlate(raw: string): string {
  return String(raw ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

interface VisitRow { id: number; plate: string; entry_ts: number; last_seen_ts: number }
interface StayRow { id: number; zone: string }

/**
 * Turns a stream of plate reads from role-tagged cameras into vehicle visits:
 *   entry camera -> opens a visit
 *   area camera  -> moves the vehicle between zones (tracks dwell per zone)
 *   exit camera  -> closes the visit; exit - entry = turnaround time
 * Emits 'read' (read + result) and 'change' after anything that alters state.
 */
export class Engine extends EventEmitter {
  constructor(private db: DB, private opts: EngineOptions) { super(); }

  // ---------- cameras ----------

  listCameras(): Camera[] {
    return this.db.prepare(`
      SELECT c.id, c.name, c.role, c.zone, c.enabled, c.last_read_at AS lastReadAt,
             (SELECT COUNT(*) FROM reads r WHERE r.camera_id = c.id) AS readCount
      FROM cameras c ORDER BY c.name`).all() as unknown as Camera[];
  }

  getCamera(id: string): Camera | undefined {
    return this.db.prepare(
      `SELECT id, name, role, zone, enabled, last_read_at AS lastReadAt FROM cameras WHERE id = ?`
    ).get(id) as unknown as Camera | undefined;
  }

  /** Add a camera if unknown; never overwrites a role someone assigned. Refreshes the name. */
  upsertCamera(id: string, name: string, enabled = true): Camera {
    this.db.prepare(`
      INSERT INTO cameras (id, name, enabled) VALUES (?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET name = excluded.name, enabled = excluded.enabled
    `).run(id, name || id, enabled ? 1 : 0);
    return this.getCamera(id)!;
  }

  setCameraRole(id: string, role: CameraRole, zone: string | null): Camera {
    if (!ROLES.includes(role)) throw new Error(`invalid role: ${role}`);
    const cleanZone = zone?.trim() || null;
    if (role === 'area' && !cleanZone) throw new Error('area cameras need a zone name');
    const r = this.db.prepare(`UPDATE cameras SET role = ?, zone = ? WHERE id = ?`).run(role, cleanZone, id);
    if (r.changes === 0) throw new Error('camera not found');
    this.emit('change');
    return this.getCamera(id)!;
  }

  // ---------- reads ----------

  processRead(read: PlateRead): ReadResult {
    const plate = normalisePlate(read.plate);
    if (plate.length < 2) return { outcome: 'unreadable' };
    if (read.confidence != null && read.confidence < this.opts.minConfidence) {
      return { outcome: 'low_confidence' };
    }

    let cam = this.getCamera(read.cameraId) ?? this.upsertCamera(read.cameraId, read.cameraName || read.cameraId);
    // A sender that knows the camera's job (PlateWatch config) can pre-allocate it; a role set in the dashboard always wins.
    if (cam.role === 'unassigned' && read.roleHint) {
      cam = this.setCameraRole(cam.id, read.roleHint, read.roleHint === 'area' ? cam.name : null);
    }
    const ts = Math.round(read.ts);
    const record = (outcome: ReadResult['outcome'], visitId?: number) => {
      this.db.prepare(`
        INSERT INTO reads (ts, plate, camera_id, role, zone, confidence, snapshot_url, source, outcome, visit_id)
        VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
        ts, plate, cam.id, cam.role, cam.zone, read.confidence, read.snapshotUrl ?? null,
        read.source, outcome, visitId ?? null);
      this.db.prepare(`UPDATE cameras SET last_read_at = MAX(COALESCE(last_read_at, 0), ?) WHERE id = ?`).run(ts, cam.id);
    };

    if (!cam.enabled || cam.role === 'ignore') return { outcome: 'ignored' };

    // Same plate on the same camera within the debounce window = the camera re-reading one vehicle.
    const recent = this.db.prepare(
      `SELECT 1 FROM reads WHERE plate = ? AND camera_id = ? AND ts BETWEEN ? AND ? LIMIT 1`
    ).get(plate, cam.id, ts - this.opts.debounceMs, ts + this.opts.debounceMs);
    if (recent) return { outcome: 'debounced' };

    let result!: ReadResult;
    this.db.exec('BEGIN');
    try {
      if (cam.role === 'unassigned') {
        record('unassigned');
        result = { outcome: 'unassigned' };
      } else if (cam.role === 'entry') result = this.onEntry(plate, cam, ts, record);
      else if (cam.role === 'exit') result = this.onExit(plate, cam, ts, record);
      else result = this.onArea(plate, cam, ts, record);
      this.db.exec('COMMIT');
    } catch (err) {
      this.db.exec('ROLLBACK');
      throw err;
    }
    this.emit('read', { read: { ...read, plate }, camera: cam, result });
    this.emit('change');
    return result;
  }

  private openVisit(plate: string): VisitRow | undefined {
    return this.db.prepare(
      `SELECT id, plate, entry_ts, last_seen_ts FROM visits WHERE plate = ? AND status = 'open' ORDER BY entry_ts DESC LIMIT 1`
    ).get(plate) as unknown as VisitRow | undefined;
  }

  private closeStays(visitId: number, ts: number) {
    this.db.prepare(`UPDATE stays SET end_ts = MAX(start_ts, ?) WHERE visit_id = ? AND end_ts IS NULL`).run(ts, visitId);
  }

  private newVisit(plate: string, cam: Camera, ts: number, inferred: boolean): number {
    const r = this.db.prepare(
      `INSERT INTO visits (plate, entry_ts, entry_camera_id, entry_inferred, last_seen_ts) VALUES (?,?,?,?,?)`
    ).run(plate, ts, cam.id, inferred ? 1 : 0, ts);
    return Number(r.lastInsertRowid);
  }

  private onEntry(plate: string, cam: Camera, ts: number, record: (o: ReadResult['outcome'], v?: number) => void): ReadResult {
    const open = this.openVisit(plate);
    if (open) {
      if (ts - open.last_seen_ts < this.opts.reentryGraceMs) {
        this.db.prepare(`UPDATE visits SET last_seen_ts = MAX(last_seen_ts, ?) WHERE id = ?`).run(ts, open.id);
        record('duplicate', open.id);
        return { outcome: 'duplicate', visitId: open.id };
      }
      // Came in again without being seen leaving: close the stale visit at its last sighting.
      this.closeStays(open.id, open.last_seen_ts);
      this.db.prepare(`UPDATE visits SET status = 'missed_exit', exit_ts = last_seen_ts WHERE id = ?`).run(open.id);
    }
    const id = this.newVisit(plate, cam, ts, false);
    record('entry', id);
    return { outcome: 'entry', visitId: id };
  }

  private onArea(plate: string, cam: Camera, ts: number, record: (o: ReadResult['outcome'], v?: number) => void): ReadResult {
    const zone = cam.zone || cam.name;
    let open = this.openVisit(plate);
    let outcome: ReadResult['outcome'] = 'area';
    let visitId: number;
    if (!open) {
      visitId = this.newVisit(plate, cam, ts, true); // entry camera missed it
      outcome = 'entry_inferred';
    } else {
      visitId = open.id;
      this.db.prepare(`UPDATE visits SET last_seen_ts = MAX(last_seen_ts, ?) WHERE id = ?`).run(ts, visitId);
    }
    const cur = this.db.prepare(
      `SELECT id, zone FROM stays WHERE visit_id = ? AND end_ts IS NULL ORDER BY start_ts DESC LIMIT 1`
    ).get(visitId) as unknown as StayRow | undefined;
    if (cur?.zone === zone) {
      record('duplicate', visitId);
      return { outcome: 'duplicate', visitId };
    }
    this.closeStays(visitId, ts);
    this.db.prepare(`INSERT INTO stays (visit_id, zone, camera_id, start_ts) VALUES (?,?,?,?)`).run(visitId, zone, cam.id, ts);
    record(outcome, visitId);
    return { outcome, visitId };
  }

  private onExit(plate: string, cam: Camera, ts: number, record: (o: ReadResult['outcome'], v?: number) => void): ReadResult {
    const open = this.openVisit(plate);
    if (!open) {
      record('orphan_exit');
      return { outcome: 'orphan_exit' };
    }
    const exitTs = Math.max(ts, open.entry_ts);
    this.closeStays(open.id, exitTs);
    this.db.prepare(
      `UPDATE visits SET status = 'closed', exit_ts = ?, exit_camera_id = ?, last_seen_ts = ? WHERE id = ?`
    ).run(exitTs, cam.id, exitTs, open.id);
    record('exit', open.id);
    return { outcome: 'exit', visitId: open.id, turnaroundMs: exitTs - open.entry_ts };
  }

  /** Manually close a visit (e.g. a vehicle left through a gate with no camera). */
  closeVisit(id: number, ts = Date.now()): boolean {
    const v = this.db.prepare(`SELECT entry_ts FROM visits WHERE id = ? AND status = 'open'`).get(id) as { entry_ts: number } | undefined;
    if (!v) return false;
    const t = Math.max(ts, v.entry_ts);
    this.closeStays(id, t);
    this.db.prepare(`UPDATE visits SET status = 'missed_exit', exit_ts = ? WHERE id = ?`).run(t, id);
    this.emit('change');
    return true;
  }

  /** Delete data older than the retention window. Returns rows removed. */
  purge(retentionDays: number, now = Date.now()): number {
    if (retentionDays <= 0) return 0;
    const cutoff = now - retentionDays * 86_400_000;
    const a = this.db.prepare(`DELETE FROM reads WHERE ts < ?`).run(cutoff).changes;
    const b = this.db.prepare(`DELETE FROM visits WHERE status != 'open' AND COALESCE(exit_ts, entry_ts) < ?`).run(cutoff).changes;
    return Number(a) + Number(b);
  }
}
