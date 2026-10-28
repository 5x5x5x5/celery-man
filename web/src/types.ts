// Mirrors celery_man/web/state.py project_state, field for field. Keep them identical.

export type Mode = "tastemaker" | "auto";
export type Kind = "footage" | "animation";
export const KINDS: Kind[] = ["footage", "animation"];
export const STEPS = ["song", "brief", "shots", "keyframes", "takes", "cut"] as const;
export type Step = (typeof STEPS)[number];

export interface ProjectInfo {
  id: string;
  song: string;
  song_source: string;
  range: [number | null, number | null];
  kinds: Kind[];
  cap_usd: number;
  modes: Record<string, Mode>;
  shot_modes: Record<string, Record<string, Mode>>;
  seed: string;
  vision_flags: boolean;
}

export interface Window {
  id: string;
  start: number;
  end: number;
  section: string;
}
export interface Timing {
  duration: number;
  windows: Window[];
}

export interface Named {
  name: string;
  description: string;
}
export interface Brief {
  logline: string;
  style: { medium: string; palette: string; lighting: string; camera: string };
  cast: Named[];
  places: Named[];
  avoid: string[];
  look: { background: string; colors: string[]; typeface: string };
}

export interface Shot {
  id: string;
  start: number;
  end: number;
  section: string;
  action: string;
  cast: string[];
  place: string | null;
  framing: string;
  kind: Kind | "open";
  scene: { template?: string; file?: string; params: Record<string, unknown> } | null;
}

export interface Pick {
  key?: string;
  clip?: string;
  in?: number;
  round: number;
  gen: number;
  quick?: boolean;
  uploaded?: boolean;
  rejected?: boolean;
  reason?: string;
}
export interface KeyframeEntry {
  path: string;
  approved: boolean | null;
  uploaded?: boolean;
  prompt_sha: string;
  [k: string]: unknown;
}
export interface ShotTake {
  key: string;
  clip: string;
  sheet: string | null;
  seed: number;
}
export interface ShotTakes {
  need: "done" | "run" | "judge" | "edit" | "limit" | "fallback";
  /** The NEXT round to generate. Not the round of `takes`. */
  round: number;
  cap: number | null;
  /** The clips of the latest LANDED round, the ones a pick is made from. */
  takes: ShotTake[];
  pick: Pick | null;
  changed_since_upload: boolean;
}
export interface ShotState {
  shot: Shot;
  open: boolean;
  modes: Record<"shots" | "keyframes" | "takes", Mode>;
  render: { need: "gen" | "failed" | "done"; clip: string | null; poster: string | null; error: string | null } | null;
  keyframe: { need: "gen" | "judge" | "done"; entry: KeyframeEntry | null; changed_since_upload: boolean } | null;
  takes: ShotTakes | null;
}

export interface StepState {
  step: string;
  state: "done" | "stale" | "missing" | "waiting";
  detail: string;
}
export interface Spend {
  spent: number;
  reserve: number;
  cap: number;
}
export interface Prices {
  continue: number;
  still: number;
  keyframe_set: number;
  take_round: number;
}
export interface Candidates {
  brief: unknown | null;
  shots: Record<string, unknown>;
  keyframes: Record<string, unknown>;
}
export interface Job {
  id: string;
  name: string;
  started: number | null;
}
export interface Jobs {
  running: Job | null;
  queued: Job[];
  free: Job[];
  errors: { id: string; name: string; error: string }[];
}
export interface CutItem {
  shot: string;
  kind: "take" | "render" | "still" | "card" | "blank";
  src: string | null;
  text: string;
  in: number;
  start: number;
  end: number;
}

export interface State {
  project: ProjectInfo;
  timing: Timing | null;
  brief: Brief | null;
  shots: ShotState[];
  steps: StepState[];
  spend: Spend;
  prices: Prices;
  candidates: Candidates;
  jobs: Jobs;
  cut: CutItem[];
}
