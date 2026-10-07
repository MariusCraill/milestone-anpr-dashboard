# Yard Traffic – Milestone XProtect ANPR dashboard

Turns plate reads from ANPR cameras managed in **Milestone XProtect** into vehicle visits and a live dashboard:
who is on site, who left, how long each trip took and how long vehicles spend in each area.

```
ANPR camera ──► XProtect (VMS) ──► this service ──► SQLite ──► dashboard
      └──────── or direct webhook ─────┘
```

## Quick start

```bash
cd vms-anpr
npm install
npm run demo        # http://localhost:3100 with 3 days of simulated traffic, no setup
```

Requires Node ≥ 22.13 (uses the built-in `node:sqlite`). Real use: copy `.env.example` to `.env`, set the values
(export them or use `node --env-file=.env`) and run `npm start`.

## How cameras are allocated

Every camera has a **role** (Cameras tab, or `PUT /api/cameras/:id`):

| Role | Effect |
|---|---|
| **Entry** | opens a visit |
| **Area** (+ zone name) | moves the vehicle into that zone; time in each zone is recorded |
| **Exit** | closes the visit; *exit − entry* = turnaround |
| **Unassigned** | default for new cameras; reads are logged but ignored |
| **Ignore** | reads are dropped |

Edge cases are handled and surfaced under “Needs attention”: repeat reads (debounce + re-entry grace), exit with no
entry, area read with no entry (entry inferred), re-entry without an exit (previous visit closed as *missed exit*, excluded
from turnaround), overstays, and a manual “Mark left”.

## Getting reads in

1. **Webhook (recommended, works with any ANPR source)** – `POST /api/ingest` with header `X-API-Key: $INGEST_API_KEY`.
   JSON; one read, an array, or `{events:[...]}`. Field names are flexible (`plate`/`licensePlate`/`plateNumber`…,
   `cameraId`/`deviceId`…, `timestamp`/`time`/`dateTime` as ISO or epoch s/ms, `confidence` 0–1 or 0–100).
   ```bash
   curl -X POST localhost:3100/api/ingest -H 'X-API-Key: change-me' -H 'Content-Type: application/json' \
     -d '{"plate":"CA 123-456","cameraId":"<milestone camera GUID>","timestamp":"2026-10-07T08:00:00Z","confidence":0.94}'
   ```
   Use the **Milestone camera GUID** as `cameraId` so names sync from XProtect; unknown cameras self-register as Unassigned.
   Point the camera, an XProtect LPR/analytics integration or a small script at this URL.
2. **Camera sync** – set `MILESTONE_BASE_URL/USERNAME/PASSWORD` and use *Sync cameras from Milestone* (calls the
   XProtect API Gateway: `/IDP/connect/token`, `/api/rest/v1/cameras`). Use a read-only account.
3. **Alarm polling (experimental)** – `MILESTONE_POLL_ALARMS=1` polls the alarm endpoint for LPR alarms. Alarm endpoints
   and payloads vary between XProtect versions, so this is a best-effort starting point; prefer the webhook if it doesn't match yours.

> **Verification status:** the engine, parser and dashboard are unit-tested / exercised with simulated data. The
> Milestone API Gateway calls have **not** been run against a real XProtect server – expect to adjust paths/fields on first connect.

## Security & privacy

- Plates are personal information (POPIA/GDPR). Data older than `RETENTION_DAYS` (default 90) is purged.
- Set `INGEST_API_KEY` (ingest is refused without it) and `DASHBOARD_USER/PASS` (basic auth – put it behind HTTPS/reverse proxy).
- CSV export neutralises spreadsheet formulas.

## Layout

`src/engine.ts` visit logic · `src/stats.ts` dashboard queries · `src/milestone/` XProtect client + payload parser ·
`src/simulator.ts` demo data · `public/` dashboard (vanilla JS + SVG, no build) · `test/` (`npm test`).
