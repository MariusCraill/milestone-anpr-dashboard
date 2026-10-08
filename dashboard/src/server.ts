import express, { NextFunction, Request, Response } from 'express';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config } from './config.ts';
import { openDb } from './db.ts';
import { Engine } from './engine.ts';
import { MilestoneClient } from './milestone/client.ts';
import { parseIngest } from './milestone/ingest.ts';
import { startSimulator } from './simulator.ts';
import { dashboardStats, recentReads, vehicleHistory, visitsCsv } from './stats.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const db = openDb(config.demo ? ':memory:' : config.dataDir);
const engine = new Engine(db, {
  debounceMs: config.debounceMs,
  reentryGraceMs: config.reentryGraceMs,
  minConfidence: config.minConfidence,
});
const milestone = new MilestoneClient();

const safeEqual = (a: string, b: string) => {
  const ha = crypto.createHash('sha256').update(a).digest();
  const hb = crypto.createHash('sha256').update(b).digest();
  return crypto.timingSafeEqual(ha, hb);
};

const app = express();
app.disable('x-powered-by');
app.use((_req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Security-Policy', "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'");
  next();
});
app.get('/api/health', (_req, res) => { res.json({ status: 'ok', time: new Date().toISOString() }); });

// ---- ingest: authenticated by API key, not by dashboard login ----
app.post('/api/ingest', express.json({ limit: '1mb', type: () => true }), (req: Request, res: Response) => {
  if (!config.ingestApiKey && !config.demo) {
    res.status(503).json({ error: 'INGEST_API_KEY is not configured' });
    return;
  }
  const key = String(req.header('x-api-key') ?? '');
  if (config.ingestApiKey && !safeEqual(key, config.ingestApiKey)) {
    res.status(401).json({ error: 'invalid API key' });
    return;
  }
  const reads = parseIngest(req.body, 'webhook');
  if (!reads.length) {
    res.status(422).json({ error: 'no plate reads recognised (need a plate and a camera id)' });
    return;
  }
  const results = reads.map(r => ({ plate: r.plate, camera: r.cameraId, ...engine.processRead(r) }));
  res.json({ accepted: results.length, results });
});

// ---- dashboard auth (optional) ----
app.use((req, res, next) => {
  if (!config.dashUser) return next();
  const [scheme, b64] = (req.header('authorization') ?? '').split(' ');
  const [u = '', ...p] = Buffer.from(b64 ?? '', 'base64').toString().split(':');
  if (scheme === 'Basic' && safeEqual(u, config.dashUser) && safeEqual(p.join(':'), config.dashPass)) return next();
  res.setHeader('WWW-Authenticate', 'Basic realm="ANPR dashboard"').status(401).send('Authentication required');
});

// State-changing dashboard calls must be JSON: forces a CORS preflight, which blocks cross-site form posts.
app.use('/api', (req, res, next) => {
  if (req.method !== 'GET' && !req.is('application/json')) {
    res.status(415).json({ error: 'Content-Type must be application/json' });
    return;
  }
  next();
});
app.use('/api', express.json({ limit: '100kb' }));

app.get('/api/stats', (_req, res) => {
  res.json({
    ...dashboardStats(db, Date.now(), config.overstayMs),
    overstayHours: config.overstayMs / 3_600_000,
    demo: config.demo,
    milestone: { configured: milestone.configured, lastError: milestone.lastError, lastCameraSync: milestone.lastCameraSync },
  });
});
app.get('/api/reads', (req, res) => {
  res.json(recentReads(db, Math.min(200, Number(req.query.limit) || 40)));
});
app.get('/api/cameras', (_req, res) => { res.json(engine.listCameras()); });
app.put('/api/cameras/:id', (req, res) => {
  try {
    res.json(engine.setCameraRole(req.params.id, req.body?.role, req.body?.zone ?? null));
  } catch (e) {
    res.status(400).json({ error: (e as Error).message });
  }
});
app.post('/api/cameras/sync', async (_req, res) => {
  try {
    res.json({ synced: await milestone.syncCameras(engine) });
  } catch (e) {
    res.status(502).json({ error: (e as Error).message });
  }
});
app.get('/api/vehicles/:plate', (req, res) => {
  res.json(vehicleHistory(db, req.params.plate.toUpperCase().replace(/[^A-Z0-9]/g, '')));
});
app.post('/api/visits/:id/close', (req, res) => {
  res.json({ closed: engine.closeVisit(Number(req.params.id)) });
});
app.get('/api/export.csv', (req, res) => {
  const to = Number(req.query.to) || Date.now() + 1;
  const from = Number(req.query.from) || to - 30 * 86_400_000;
  res.type('text/csv').attachment('visits.csv').send(visitsCsv(db, from, to));
});

// ---- live updates (server-sent events); clients refetch on 'change' ----
const clients = new Set<Response>();
app.get('/api/stream', (req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
  res.write('retry: 3000\n\n');
  clients.add(res);
  req.on('close', () => clients.delete(res));
});
let pending: NodeJS.Timeout | null = null;
engine.on('change', () => {
  if (pending) return;
  pending = setTimeout(() => {
    pending = null;
    for (const c of clients) c.write('event: change\ndata: {}\n\n');
  }, 400);
});
setInterval(() => { for (const c of clients) c.write(': ping\n\n'); }, 25_000).unref();

app.use(express.static(path.join(here, '..', 'public')));
app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  const status = (err as any).status ?? 500;
  res.status(status).json({ error: status === 500 ? 'internal error' : err.message });
  if (status === 500) console.error(err);
});

app.listen(config.port, () => {
  console.log(`ANPR dashboard on http://localhost:${config.port}${config.demo ? '  (DEMO data)' : ''}`);
  if (config.demo) startSimulator(engine);
  else {
    if (milestone.configured) {
      milestone.syncCameras(engine).then(n => console.log(`Milestone: ${n} cameras synced`)).catch(e => console.warn(e.message));
      milestone.startAlarmPolling(engine);
    }
    const purge = () => { const n = engine.purge(config.retentionDays); if (n) console.log(`retention: purged ${n} rows`); };
    purge();
    setInterval(purge, 6 * 3_600_000).unref();
  }
});
