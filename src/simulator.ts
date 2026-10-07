import type { Engine } from './engine.ts';

const MIN = 60_000, HOUR = 3_600_000, DAY = 24 * HOUR;

export const DEMO_CAMERAS = [
  { id: 'demo-cam-gate-in', name: 'Main Gate – IN', role: 'entry', zone: 'Main Gate' },
  { id: 'demo-cam-queue', name: 'Yard Queue', role: 'area', zone: 'Queue' },
  { id: 'demo-cam-wb', name: 'Weighbridge', role: 'area', zone: 'Weighbridge' },
  { id: 'demo-cam-bay', name: 'Loading Bays', role: 'area', zone: 'Loading Bay' },
  { id: 'demo-cam-gate-out', name: 'Main Gate – OUT', role: 'exit', zone: 'Main Gate' },
] as const;

// Small seeded PRNG so demo data is stable between runs.
function rng(seed: number) {
  return () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 2 ** 32;
  };
}

const L = 'ABCDFGHJKLMNPRSTVWXYZ'.split('');

/**
 * Seeds ~3 days of history and keeps playing out journeys in real time, so vehicles seen
 * "now" are mid-journey and leave at their scheduled time. Good for demos and UI work.
 */
export function startSimulator(engine: Engine, now = Date.now()) {
  const rand = rng(42);
  const pick = <T,>(a: readonly T[]) => a[Math.floor(rand() * a.length)];
  const plate = () => `${pick(L)}${pick(L)}${10 + Math.floor(rand() * 89)}${pick(L)}${pick(L)}GP`;

  for (const c of DEMO_CAMERAS) {
    engine.upsertCamera(c.id, c.name);
    engine.setCameraRole(c.id, c.role, c.zone);
  }

  const fleet = Array.from({ length: 22 }, plate);
  const events: { ts: number; camera: string; plate: string }[] = [];
  const cam = (n: string) => DEMO_CAMERAS.find(c => c.id === `demo-cam-${n}`)!.id;
  const lognormal = (medianMin: number, spread: number) =>
    medianMin * MIN * Math.exp((rand() + rand() + rand() - 1.5) * spread);

  const addJourney = (t0: number, p: string, opts: { duration?: number; skipEntry?: boolean } = {}) => {
    const total = Math.max(12 * MIN, opts.duration ?? lognormal(55, 1.1));
    const stops: [number, string][] = [
      [0, 'gate-in'], [(2 + rand() * 6) * MIN, 'queue'], [total * (0.18 + rand() * 0.1), 'wb'],
      [total * (0.45 + rand() * 0.15), 'bay'], [total * (0.75 + rand() * 0.08), 'wb'],
    ];
    stops.forEach(([off, c], i) => { if (!(i === 0 && opts.skipEntry)) events.push({ ts: t0 + off, camera: cam(c), plate: p }); });
    events.push({ ts: t0 + total, camera: cam('gate-out'), plate: p });
  };

  // arrivals: busy 06:00-17:00, trickle otherwise
  for (let d = 3; d >= 0; d--) {
    const day = new Date(now - d * DAY); day.setHours(0, 0, 0, 0);
    for (let h = 0; h < 24; h++) {
      const busy = h >= 6 && h < 17 ? 4 : h >= 17 && h < 21 ? 1.2 : 0.2;
      const n = Math.floor(busy + rand() * 2);
      for (let i = 0; i < n; i++) {
        const t0 = day.getTime() + h * HOUR + rand() * HOUR;
        if (t0 > now + HOUR) continue;
        addJourney(t0, rand() < 0.4 ? pick(fleet) : plate(), { skipEntry: rand() < 0.04 });
      }
    }
  }
  // one vehicle that arrived 9 h ago and never left -> shows as an overstay
  addJourney(now - 9 * HOUR, plate(), { duration: 40 * DAY });
  events.sort((a, b) => a.ts - b.ts);

  // Drop the leftover events of "forever" journey and anything >1.5h out; the rest play in real time.
  const past = events.filter(e => e.ts <= now);
  const future = events.filter(e => e.ts > now && e.ts < now + 2 * HOUR);
  for (const e of past) engine.processRead({ plate: e.plate, plateRaw: e.plate, cameraId: e.camera, ts: e.ts, confidence: 88 + rand() * 11, source: 'demo' });

  const timers = future.map(e => setTimeout(() =>
    engine.processRead({ plate: e.plate, plateRaw: e.plate, cameraId: e.camera, ts: Date.now(), confidence: 88 + rand() * 11, source: 'demo' }),
    Math.max(0, e.ts - now)));

  // keep the demo alive: a new arrival every ~40-90s
  const loop = setInterval(() => {
    const p = rand() < 0.4 ? pick(fleet) : plate();
    const dur = lognormal(4, 0.6); // compressed so a live demo shows exits too
    addLive(p, dur);
  }, 45_000);
  const addLive = (p: string, total: number) => {
    const seq: [number, string][] = [[0, 'gate-in'], [total * 0.2, 'queue'], [total * 0.45, 'wb'], [total * 0.7, 'bay'], [total, 'gate-out']];
    for (const [off, c] of seq) timers.push(setTimeout(() =>
      engine.processRead({ plate: p, plateRaw: p, cameraId: cam(c), ts: Date.now(), confidence: 90 + rand() * 9, source: 'demo' }), off));
  };
  return () => { clearInterval(loop); timers.forEach(clearTimeout); };
}
