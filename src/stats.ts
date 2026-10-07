import type { DB } from './db.ts';

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export function startOfDay(ts: number): number {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function percentile(sorted: number[], p: number): number | null {
  if (!sorted.length) return null;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[i];
}

export function summarise(values: number[]) {
  const s = [...values].sort((a, b) => a - b);
  return {
    count: s.length,
    avg: s.length ? Math.round(s.reduce((a, b) => a + b, 0) / s.length) : null,
    median: percentile(s, 0.5),
    p90: percentile(s, 0.9),
    min: s[0] ?? null,
    max: s[s.length - 1] ?? null,
  };
}

export const TURNAROUND_BUCKETS = [
  { label: '<15m', max: 15 * 60_000 },
  { label: '15–30m', max: 30 * 60_000 },
  { label: '30–60m', max: HOUR },
  { label: '1–2h', max: 2 * HOUR },
  { label: '2–4h', max: 4 * HOUR },
  { label: '4–8h', max: 8 * HOUR },
  { label: '8h+', max: Infinity },
];

type Row = Record<string, any>;

export function dashboardStats(db: DB, now: number, overstayMs: number) {
  const q = (sql: string, ...p: any[]) => db.prepare(sql).all(...p) as Row[];
  const today = startOfDay(now);
  const weekAgo = today - 6 * DAY;

  // ---- on site now ----
  const onSite = q(`
    SELECT v.id, v.plate, v.entry_ts, v.entry_inferred, v.last_seen_ts, ec.name AS entry_camera,
      (SELECT zone FROM stays s WHERE s.visit_id = v.id AND s.end_ts IS NULL ORDER BY s.start_ts DESC LIMIT 1) AS zone,
      (SELECT start_ts FROM stays s WHERE s.visit_id = v.id AND s.end_ts IS NULL ORDER BY s.start_ts DESC LIMIT 1) AS zone_since
    FROM visits v LEFT JOIN cameras ec ON ec.id = v.entry_camera_id
    WHERE v.status = 'open' ORDER BY v.entry_ts ASC`).map(r => ({
    id: r.id, plate: r.plate, entryTs: r.entry_ts, entryCamera: r.entry_camera,
    entryInferred: !!r.entry_inferred, lastSeenTs: r.last_seen_ts,
    zone: r.zone ?? null, zoneSince: r.zone_since ?? null,
    durationMs: now - r.entry_ts, overstay: now - r.entry_ts > overstayMs,
  }));

  // ---- today's counts ----
  const entriesToday = q(`SELECT COUNT(*) n FROM visits WHERE entry_ts >= ?`, today)[0].n as number;
  const exitsToday = q(`SELECT COUNT(*) n FROM visits WHERE status = 'closed' AND exit_ts >= ?`, today)[0].n as number;

  // ---- turnaround (closed visits only: an exit camera really saw them leave) ----
  const closed = q(`SELECT entry_ts, exit_ts FROM visits WHERE status = 'closed' AND exit_ts >= ?`, weekAgo);
  const durations = (r: Row[]) => r.map(x => x.exit_ts - x.entry_ts);
  const turnaroundToday = summarise(durations(closed.filter(r => r.exit_ts >= today)));
  const turnaroundWeek = summarise(durations(closed));

  const histogram = TURNAROUND_BUCKETS.map(b => ({ label: b.label, count: 0 }));
  for (const d of durations(closed)) {
    histogram[TURNAROUND_BUCKETS.findIndex(b => d < b.max)].count++;
  }

  const daily: { day: number; avg: number | null; count: number }[] = [];
  for (let i = 6; i >= 0; i--) {
    const from = today - i * DAY;
    const ds = durations(closed.filter(r => r.exit_ts >= from && r.exit_ts < from + DAY));
    daily.push({ day: from, avg: summarise(ds).avg, count: ds.length });
  }

  // ---- hourly flow + occupancy over last 24 h (hour-aligned) ----
  const hourStart = Math.floor(now / HOUR) * HOUR;
  const first = hourStart - 23 * HOUR;
  const visits24 = q(`
    SELECT entry_ts, exit_ts, status FROM visits
    WHERE entry_ts < ? AND (status = 'open' OR exit_ts >= ?)`, hourStart + HOUR, first);
  const hourly = Array.from({ length: 24 }, (_, i) => {
    const t = first + i * HOUR, end = t + HOUR;
    let inN = 0, outN = 0, onSiteEnd = 0;
    for (const v of visits24) {
      if (v.entry_ts >= t && v.entry_ts < end) inN++;
      if (v.status === 'closed' && v.exit_ts >= t && v.exit_ts < end) outN++;
      const left = v.exit_ts == null ? Infinity : v.exit_ts;
      if (v.entry_ts < Math.min(end, now) && left >= Math.min(end, now)) onSiteEnd++;
    }
    return { hour: t, in: inN, out: outN, onSite: onSiteEnd };
  });
  const peakOnSite = hourly.reduce((m, h) => Math.max(m, h.onSite), 0);

  // ---- zones ----
  const zoneNow = q(`SELECT zone, COUNT(*) n FROM stays WHERE end_ts IS NULL
    AND visit_id IN (SELECT id FROM visits WHERE status = 'open') GROUP BY zone`);
  const zoneDwell = q(`SELECT zone, COUNT(*) n, AVG(end_ts - start_ts) avg, MAX(end_ts - start_ts) max
    FROM stays WHERE end_ts IS NOT NULL AND start_ts >= ? GROUP BY zone`, weekAgo);
  const zoneNames = new Set<string>([
    ...zoneNow.map(z => z.zone), ...zoneDwell.map(z => z.zone),
    ...q(`SELECT DISTINCT zone FROM cameras WHERE role = 'area' AND zone IS NOT NULL`).map(z => z.zone),
  ]);
  const zones = [...zoneNames].sort().map(name => {
    const now_ = zoneNow.find(z => z.zone === name);
    const d = zoneDwell.find(z => z.zone === name);
    return {
      zone: name, occupancy: now_?.n ?? 0, visits: d?.n ?? 0,
      avgDwellMs: d ? Math.round(d.avg) : null, maxDwellMs: d?.max ?? null,
    };
  });

  // ---- exceptions today ----
  const ex = (outcome: string) =>
    q(`SELECT COUNT(*) n FROM reads WHERE outcome = ? AND ts >= ?`, outcome, today)[0].n as number;
  const exceptions = {
    orphanExits: ex('orphan_exit'),
    inferredEntries: ex('entry_inferred'),
    unassignedReads: ex('unassigned'),
    missedExits: q(`SELECT COUNT(*) n FROM visits WHERE status = 'missed_exit' AND entry_ts >= ?`, today)[0].n as number,
    overstay: onSite.filter(v => v.overstay).length,
  };

  // ---- repeat visitors (last 7 days) ----
  const frequent = q(`SELECT plate, COUNT(*) n FROM visits WHERE entry_ts >= ? GROUP BY plate HAVING n > 1
    ORDER BY n DESC, plate LIMIT 5`, weekAgo).map(r => ({ plate: r.plate, visits: r.n }));

  const cameras = q(`SELECT id, name, role, zone, enabled, last_read_at AS lastReadAt FROM cameras ORDER BY name`);

  return {
    now, onSite, entriesToday, exitsToday, peakOnSite,
    turnaroundToday, turnaroundWeek, histogram, daily, hourly,
    zones, exceptions, frequent, cameras,
  };
}

export function recentReads(db: DB, limit = 40) {
  return (db.prepare(`
    SELECT r.id, r.ts, r.plate, r.camera_id AS cameraId, c.name AS cameraName, r.role, r.zone,
           r.confidence, r.snapshot_url AS snapshotUrl, r.outcome, r.visit_id AS visitId
    FROM reads r LEFT JOIN cameras c ON c.id = r.camera_id
    ORDER BY r.ts DESC, r.id DESC LIMIT ?`).all(limit) as Row[]);
}

export function vehicleHistory(db: DB, plate: string) {
  const visits = db.prepare(`SELECT id, plate, entry_ts AS entryTs, exit_ts AS exitTs, status, entry_inferred AS entryInferred
    FROM visits WHERE plate = ? ORDER BY entry_ts DESC LIMIT 50`).all(plate) as Row[];
  const stays = db.prepare(`SELECT visit_id AS visitId, zone, start_ts AS startTs, end_ts AS endTs FROM stays
    WHERE visit_id IN (SELECT id FROM visits WHERE plate = ? ORDER BY entry_ts DESC LIMIT 50) ORDER BY start_ts`).all(plate) as Row[];
  return visits.map(v => ({ ...v, stays: stays.filter(s => s.visitId === v.id) }));
}

export function visitsCsv(db: DB, from: number, to: number): string {
  const rows = db.prepare(`
    SELECT v.id, v.plate, v.entry_ts, v.exit_ts, v.status, v.entry_inferred, ec.name entry_cam, xc.name exit_cam
    FROM visits v LEFT JOIN cameras ec ON ec.id = v.entry_camera_id LEFT JOIN cameras xc ON xc.id = v.exit_camera_id
    WHERE v.entry_ts >= ? AND v.entry_ts < ? ORDER BY v.entry_ts`).all(from, to) as Row[];
  const esc = (v: any) => {
    let s = v == null ? '' : String(v);
    if (/^[=+\-@]/.test(s)) s = "'" + s;           // neutralise spreadsheet formulas
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const iso = (t: number | null) => (t == null ? '' : new Date(t).toISOString());
  const lines = ['visit_id,plate,entry_time,exit_time,turnaround_minutes,status,entry_inferred,entry_camera,exit_camera'];
  for (const r of rows) {
    const mins = r.exit_ts != null && r.status === 'closed' ? ((r.exit_ts - r.entry_ts) / 60000).toFixed(1) : '';
    lines.push([r.id, r.plate, iso(r.entry_ts), iso(r.exit_ts), mins, r.status, r.entry_inferred, r.entry_cam, r.exit_cam].map(esc).join(','));
  }
  return lines.join('\n') + '\n';
}
