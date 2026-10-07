import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openDb } from '../src/db.ts';
import { Engine } from '../src/engine.ts';
import { dashboardStats } from '../src/stats.ts';
import { parseIngest } from '../src/milestone/ingest.ts';

const MIN = 60_000;
const T0 = new Date('2026-10-07T08:00:00').getTime();

function setup() {
  const db = openDb(':memory:');
  const e = new Engine(db, { debounceMs: 30_000, reentryGraceMs: 5 * MIN, minConfidence: 50 });
  for (const [id, role, zone] of [
    ['in', 'entry', null], ['out', 'exit', null], ['wb', 'area', 'Weighbridge'], ['bay', 'area', 'Bay'],
  ] as const) { e.upsertCamera(id, id); e.setCameraRole(id, role, zone); }
  const read = (plate: string, cameraId: string, ts: number, confidence: number | null = 90) =>
    e.processRead({ plate, plateRaw: plate, cameraId, ts, confidence, source: 'test' });
  return { db, e, read };
}

test('entry -> area -> exit gives turnaround and zone dwell', () => {
  const { db, e, read } = setup();
  assert.equal(read('ab 12 cd gp', 'in', T0).outcome, 'entry');
  assert.equal(read('AB12CDGP', 'wb', T0 + 10 * MIN).outcome, 'area');
  assert.equal(read('AB12CDGP', 'bay', T0 + 25 * MIN).outcome, 'area');
  const x = read('AB12CDGP', 'out', T0 + 70 * MIN);
  assert.equal(x.outcome, 'exit');
  assert.equal(x.turnaroundMs, 70 * MIN);
  const s = dashboardStats(db, T0 + 80 * MIN, 8 * 3600_000);
  assert.equal(s.onSite.length, 0);
  assert.equal(s.turnaroundToday.avg, 70 * MIN);
  assert.equal(s.zones.find(z => z.zone === 'Weighbridge')!.avgDwellMs, 15 * MIN);
  assert.equal(s.zones.find(z => z.zone === 'Bay')!.avgDwellMs, 45 * MIN);
  assert.deepEqual([s.entriesToday, s.exitsToday], [1, 1]);
});

test('debounce and re-entry grace ignore repeat reads', () => {
  const { e, read } = setup();
  read('XY99ZZ', 'in', T0);
  assert.equal(read('XY99ZZ', 'in', T0 + 10_000).outcome, 'debounced');
  assert.equal(read('XY99ZZ', 'in', T0 + 2 * MIN).outcome, 'duplicate');
  assert.equal(e.listCameras().find(c => c.id === 'in')!.readCount, 2); // debounced one not stored
});

test('second entry without exit closes the stale visit as missed_exit', () => {
  const { db, read } = setup();
  read('XY99ZZ', 'in', T0);
  assert.equal(read('XY99ZZ', 'in', T0 + 3 * 3600_000).outcome, 'entry');
  const rows = db.prepare(`SELECT status FROM visits ORDER BY id`).all().map((r: any) => r.status);
  assert.deepEqual(rows, ['missed_exit', 'open']);
});

test('exit with no visit is an orphan; area with no visit infers entry', () => {
  const { read } = setup();
  assert.equal(read('NOENTRY1', 'out', T0).outcome, 'orphan_exit');
  assert.equal(read('NOENTRY2', 'wb', T0).outcome, 'entry_inferred');
});

test('low confidence, unreadable, unassigned and ignored reads never create visits', () => {
  const { e, db, read } = setup();
  assert.equal(read('LOWCONF1', 'in', T0, 10).outcome, 'low_confidence');
  assert.equal(read('?', 'in', T0).outcome, 'unreadable');
  assert.equal(read('NEWCAM01', 'brand-new', T0).outcome, 'unassigned');
  e.upsertCamera('x', 'x'); e.setCameraRole('x', 'ignore', null);
  assert.equal(read('IGNORED1', 'x', T0).outcome, 'ignored');
  assert.equal((db.prepare('SELECT COUNT(*) n FROM visits').get() as any).n, 0);
});

test('area cameras require a zone; overstay is flagged', () => {
  const { e, db, read } = setup();
  assert.throws(() => e.setCameraRole('wb', 'area', ' '));
  read('OVER0001', 'in', T0);
  const s = dashboardStats(db, T0 + 9 * 3600_000, 8 * 3600_000);
  assert.equal(s.onSite[0].overstay, true);
  assert.equal(s.exceptions.overstay, 1);
});

test('parseIngest handles generic, wrapped, nested and Milestone-alarm payloads', () => {
  const now = 1_700_000_000_000;
  const a = parseIngest({ plate: 'ab 12', cameraId: 'c1', timestamp: '2026-10-07T08:00:00Z', confidence: 0.93 }, 't', now);
  assert.equal(a[0].ts, Date.parse('2026-10-07T08:00:00Z'));
  assert.equal(a[0].confidence, 93);
  const b = parseIngest({ events: [{ licensePlate: 'X1', deviceId: 'd', time: 1_700_000_100 }, { junk: true }] }, 't', now);
  assert.equal(b.length, 1);
  assert.equal(b[0].ts, 1_700_000_100_000);
  const c = parseIngest({ ANPR: { licensePlate: 'ZZ99' }, cameraId: 'c2' }, 't', now);
  assert.equal(c[0].plate, 'ZZ99');
  const d = parseIngest({ id: 'al1', message: 'LPR: plate ABC 123 GP detected', source: { id: 'guid-1', name: 'Gate' } }, 't', now);
  assert.equal(d[0].plate, 'ABC 123 GP');
  assert.equal(d[0].cameraName, 'Gate');
  assert.equal(parseIngest('nope', 't').length, 0);
  assert.equal(parseIngest({ plate: 'A1', cameraId: 'c', snapshotUrl: 'javascript:alert(1)' }, 't')[0].snapshotUrl, null);
});
