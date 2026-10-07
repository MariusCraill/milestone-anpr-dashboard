import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';

export type DB = DatabaseSync;

/** Open (and migrate) the database. Pass ':memory:' for tests. */
export function openDb(dataDir: string): DB {
  let file = ':memory:';
  if (dataDir !== ':memory:') {
    fs.mkdirSync(dataDir, { recursive: true });
    file = path.join(dataDir, 'anpr.db');
  }
  const db = new DatabaseSync(file);
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS cameras (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'unassigned',
      zone TEXT,
      enabled INTEGER NOT NULL DEFAULT 1,
      last_read_at INTEGER
    );

    -- every accepted read, with what the engine did with it
    CREATE TABLE IF NOT EXISTS reads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts INTEGER NOT NULL,
      plate TEXT NOT NULL,
      camera_id TEXT NOT NULL,
      role TEXT NOT NULL,
      zone TEXT,
      confidence REAL,
      snapshot_url TEXT,
      source TEXT,
      outcome TEXT NOT NULL,
      visit_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS reads_ts ON reads(ts);
    CREATE INDEX IF NOT EXISTS reads_plate ON reads(plate, ts);

    -- one row per trip through the site
    CREATE TABLE IF NOT EXISTS visits (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      plate TEXT NOT NULL,
      entry_ts INTEGER NOT NULL,
      entry_camera_id TEXT,
      entry_inferred INTEGER NOT NULL DEFAULT 0,
      exit_ts INTEGER,
      exit_camera_id TEXT,
      last_seen_ts INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'open'   -- open | closed | missed_exit
    );
    CREATE INDEX IF NOT EXISTS visits_open ON visits(status, plate);
    CREATE INDEX IF NOT EXISTS visits_entry ON visits(entry_ts);
    CREATE INDEX IF NOT EXISTS visits_exit ON visits(exit_ts);

    -- time spent in each area during a visit
    CREATE TABLE IF NOT EXISTS stays (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      visit_id INTEGER NOT NULL REFERENCES visits(id) ON DELETE CASCADE,
      zone TEXT NOT NULL,
      camera_id TEXT NOT NULL,
      start_ts INTEGER NOT NULL,
      end_ts INTEGER
    );
    CREATE INDEX IF NOT EXISTS stays_visit ON stays(visit_id);
    CREATE INDEX IF NOT EXISTS stays_zone ON stays(zone, start_ts);
  `);
  return db;
}
