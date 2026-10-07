import { config } from '../config.ts';
import type { Engine } from '../engine.ts';
import { parseIngest } from './ingest.ts';

interface Token { value: string; expiresAt: number }

/**
 * Minimal client for the XProtect API Gateway (REST + OpenID Connect IDP).
 * Uses the password grant with the built-in "GrantValidatorClient" - basic or Windows users that the
 * IDP accepts. Give the account read-only access to cameras (and alarms, if you poll them).
 */
export class MilestoneClient {
  private token: Token | null = null;
  private seenAlarms = new Set<string>();
  private pollTimer: NodeJS.Timeout | null = null;
  lastError: string | null = null;
  lastCameraSync: number | null = null;

  get configured() {
    const m = config.milestone;
    return !!(m.baseUrl && m.username && m.password);
  }

  private async getToken(): Promise<string> {
    if (this.token && this.token.expiresAt > Date.now() + 30_000) return this.token.value;
    const m = config.milestone;
    const res = await fetch(`${m.baseUrl}/IDP/connect/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'password', username: m.username, password: m.password, client_id: 'GrantValidatorClient',
      }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) throw new Error(`Milestone login failed: HTTP ${res.status}`);
    const j = (await res.json()) as { access_token: string; expires_in?: number };
    this.token = { value: j.access_token, expiresAt: Date.now() + (j.expires_in ?? 3600) * 1000 };
    return this.token.value;
  }

  private async get(path: string): Promise<any> {
    const res = await fetch(`${config.milestone.baseUrl}${path}`, {
      headers: { Authorization: `Bearer ${await this.getToken()}`, Accept: 'application/json' },
      signal: AbortSignal.timeout(15_000),
    });
    if (res.status === 401) this.token = null;
    if (!res.ok) throw new Error(`Milestone GET ${path}: HTTP ${res.status}`);
    return res.json();
  }

  /** Pull the camera list and register any new cameras (as 'unassigned'). Existing roles are kept. */
  async syncCameras(engine: Engine): Promise<number> {
    if (!this.configured) throw new Error('Milestone is not configured (MILESTONE_BASE_URL / USERNAME / PASSWORD)');
    try {
      const j = await this.get('/api/rest/v1/cameras');
      const list: any[] = j.array ?? j.data ?? (Array.isArray(j) ? j : []);
      let n = 0;
      for (const c of list) {
        if (!c.id) continue;
        engine.upsertCamera(String(c.id), c.displayName || c.name || String(c.id), c.enabled !== false);
        n++;
      }
      this.lastCameraSync = Date.now();
      this.lastError = null;
      return n;
    } catch (e) {
      this.lastError = (e as Error).message;
      throw e;
    }
  }

  /**
   * EXPERIMENTAL: poll the alarm list for LPR alarms and feed them through the same parser as the webhook.
   * The alarm endpoint and payload differ between XProtect versions; if yours differs, use the webhook
   * (POST /api/ingest) from a Milestone rule/plug-in or straight from the ANPR camera instead.
   */
  startAlarmPolling(engine: Engine) {
    if (!config.milestone.pollAlarms || !this.configured || this.pollTimer) return;
    let first = true;
    const tick = async () => {
      try {
        const j = await this.get(config.milestone.alarmsPath);
        const list: any[] = j.array ?? j.data ?? (Array.isArray(j) ? j : []);
        for (const a of list) {
          const key = String(a.id ?? JSON.stringify(a));
          if (this.seenAlarms.has(key)) continue;
          this.seenAlarms.add(key);
          if (first) continue; // existing backlog: remember it, don't replay it as live traffic
          for (const r of parseIngest(a, 'milestone-alarm')) engine.processRead(r);
        }
        first = false;
        if (this.seenAlarms.size > 5000) this.seenAlarms = new Set([...this.seenAlarms].slice(-2500));
        this.lastError = null;
      } catch (e) {
        this.lastError = (e as Error).message;
      }
    };
    this.pollTimer = setInterval(tick, Math.max(1, config.milestone.pollSeconds) * 1000);
    void tick();
  }

  stop() { if (this.pollTimer) clearInterval(this.pollTimer); this.pollTimer = null; }
}
