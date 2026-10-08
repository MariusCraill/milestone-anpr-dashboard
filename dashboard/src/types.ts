export type CameraRole = 'entry' | 'exit' | 'area' | 'unassigned' | 'ignore';

export interface Camera {
  id: string;            // Milestone camera GUID (or any stable id from the ANPR device)
  name: string;
  role: CameraRole;
  zone: string | null;   // area name for role=area, optional gate/lane label otherwise
  enabled: number;
  lastReadAt: number | null;
  readCount?: number;
}

/** A plate read after normalisation, regardless of where it came from. */
export interface PlateRead {
  plate: string;         // normalised: upper-case alphanumerics only
  plateRaw: string;
  cameraId: string;
  cameraName?: string;
  ts: number;            // epoch ms
  confidence: number | null;
  snapshotUrl?: string | null;
  source: string;
  roleHint?: 'entry' | 'exit' | 'area';   // e.g. from PlateWatch; only used while the camera is still unassigned
}

export type ReadOutcome =
  | 'entry' | 'exit' | 'area'          // applied to a visit
  | 'entry_inferred'                   // area/exit seen with no visit: visit created
  | 'duplicate' | 'debounced'          // ignored repeat
  | 'orphan_exit'                      // exit with no open visit
  | 'low_confidence' | 'unassigned' | 'ignored' | 'unreadable';

export interface ReadResult {
  outcome: ReadOutcome;
  visitId?: number;
  turnaroundMs?: number;
}
