const num = (v: string | undefined, d: number) => {
  const n = Number(v);
  return v !== undefined && v !== '' && Number.isFinite(n) ? n : d;
};
const e = process.env;

export const config = {
  port: num(e.PORT, 3100),
  dataDir: e.DATA_DIR || './data',
  ingestApiKey: e.INGEST_API_KEY || '',
  dashUser: e.DASHBOARD_USER || '',
  dashPass: e.DASHBOARD_PASS || '',
  milestone: {
    baseUrl: (e.MILESTONE_BASE_URL || '').replace(/\/+$/, ''),
    username: e.MILESTONE_USERNAME || '',
    password: e.MILESTONE_PASSWORD || '',
    pollAlarms: e.MILESTONE_POLL_ALARMS === '1',
    alarmsPath: e.MILESTONE_ALARMS_PATH || '/api/rest/v1/alarms',
    pollSeconds: num(e.MILESTONE_POLL_SECONDS, 3),
  },
  debounceMs: num(e.DEBOUNCE_SECONDS, 30) * 1000,
  reentryGraceMs: num(e.REENTRY_GRACE_SECONDS, 300) * 1000,
  overstayMs: num(e.OVERSTAY_HOURS, 8) * 3600_000,
  retentionDays: num(e.RETENTION_DAYS, 90),
  minConfidence: num(e.MIN_CONFIDENCE, 0),
  demo: e.DEMO === '1',
};
