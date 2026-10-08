import type { PlateRead } from '../types.ts';

type Obj = Record<string, any>;

const PLATE_KEYS = ['plate', 'licensePlate', 'license_plate', 'plateNumber', 'plate_number', 'lpr', 'vrm', 'registration', 'number'];
const CAM_ID_KEYS = ['cameraId', 'camera_id', 'cameraGuid', 'cameraGUID', 'deviceId', 'device_id', 'channelId', 'sourceId'];
const CAM_NAME_KEYS = ['cameraName', 'camera_name', 'deviceName', 'device_name', 'channelName', 'sourceName'];
const TIME_KEYS = ['timestamp', 'time', 'dateTime', 'datetime', 'eventTime', 'capturedAt', 'ts'];
const CONF_KEYS = ['confidence', 'plateConfidence', 'conf', 'score', 'probability'];
const SNAP_KEYS = ['snapshotUrl', 'snapshot_url', 'imageUrl', 'image_url', 'picture'];
const NESTED = ['data', 'event', 'payload', 'ANPR', 'anpr', 'lpr', 'EventNotificationAlert', 'plateResult'];
const LIST_KEYS = ['reads', 'events', 'array', 'data', 'items', 'alarms'];

function pick(o: Obj, keys: string[]): unknown {
  const scopes = [o, ...NESTED.map(k => o[k]).filter(v => v && typeof v === 'object' && !Array.isArray(v))];
  for (const s of scopes) for (const k of keys) {
    const v = s[k];
    if (v !== undefined && v !== null && v !== '' && typeof v !== 'object') return v;
  }
  return undefined;
}

export function parseTimestamp(v: unknown, fallback: number): number {
  if (typeof v === 'number' && Number.isFinite(v)) return v < 1e11 ? Math.round(v * 1000) : v; // seconds vs ms
  if (typeof v === 'string' && v.trim()) {
    if (/^\d+(\.\d+)?$/.test(v.trim())) return parseTimestamp(Number(v), fallback);
    const t = Date.parse(v);
    if (Number.isFinite(t)) return t;
  }
  return fallback;
}

function parseConfidence(v: unknown): number | null {
  const n = typeof v === 'string' ? parseFloat(v) : (v as number);
  if (typeof n !== 'number' || !Number.isFinite(n)) return null;
  return n <= 1 ? Math.round(n * 1000) / 10 : Math.min(100, n); // 0-1 or 0-100 -> percent
}

/** Plate from free text such as a Milestone alarm message: "LPR: plate ABC 123 GP detected". */
function plateFromText(text: unknown): string | undefined {
  if (typeof text !== 'string') return undefined;
  const quoted = /plate\W+(?:number\W+)?["'“‘]([A-Z0-9 \-]{2,12})["'”’]/i.exec(text);
  if (quoted) return quoted[1];
  const m = /plate\W+(?:(?:number|detected|read)\W+)?([A-Z0-9]+(?:[ -][A-Z0-9]+){0,3})/i.exec(text);
  if (!m) return undefined;
  // Trailing plain words ("detected", "at") are prose, not plate groups; real groups are short or hold digits.
  const groups = m[1].split(/[ -]/);
  while (groups.length > 1 && /^[A-Z]{4,}$/i.test(groups[groups.length - 1])) groups.pop();
  return /^[A-Z]{4,}$/i.test(groups[0]) ? undefined : groups.join(' ');
}

/**
 * Normalise whatever a camera, Milestone rule/plug-in or script POSTs into PlateReads.
 * Accepts a single object, an array, or a wrapper like {events:[...]}. Unrecognised items are skipped.
 */
export function parseIngest(body: unknown, source: string, now = Date.now()): PlateRead[] {
  const items: Obj[] = [];
  const walk = (b: unknown, depth: number) => {
    if (Array.isArray(b)) { b.forEach(x => walk(x, depth + 1)); return; }
    if (!b || typeof b !== 'object' || depth > 3) return;
    const o = b as Obj;
    const list = LIST_KEYS.map(k => o[k]).find(Array.isArray);
    if (list && pick(o, PLATE_KEYS) === undefined) { walk(list, depth + 1); return; }
    items.push(o);
  };
  walk(body, 0);

  const out: PlateRead[] = [];
  for (const o of items) {
    const rawPlate = pick(o, PLATE_KEYS) ?? plateFromText(pick(o, ['message', 'description', 'name']));
    const src = o.source && typeof o.source === 'object' ? (o.source as Obj) : undefined; // Milestone alarm style
    const cameraId = pick(o, CAM_ID_KEYS) ?? src?.id;
    if (rawPlate === undefined || cameraId === undefined) continue;
    const snap = pick(o, SNAP_KEYS);
    out.push({
      plate: String(rawPlate),
      plateRaw: String(rawPlate),
      cameraId: String(cameraId),
      cameraName: (pick(o, CAM_NAME_KEYS) ?? src?.name) as string | undefined,
      ts: parseTimestamp(pick(o, TIME_KEYS), now),
      confidence: parseConfidence(pick(o, CONF_KEYS)),
      snapshotUrl: typeof snap === 'string' && /^https?:\/\//i.test(snap) ? snap : null,
      source,
      roleHint: ['entry', 'exit', 'area'].includes(String(o.role)) ? (o.role as PlateRead['roleHint']) : undefined,
    });
  }
  return out;
}
