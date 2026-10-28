import type { Brief, Kind, Shot, State } from "./types";

export type Paid = { ok: true; job?: string } | { ok: false; price?: number; reason?: string };

export class ApiError extends Error {}

const base = (pid: string) => `/api/projects/${encodeURIComponent(pid)}`;
export const fileUrl = (pid: string, path: string) => `${base(pid)}/files/${path}`;

async function call(method: string, url: string, body?: unknown): Promise<Response> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return fetch(url, init);
}

async function fail(r: Response): Promise<never> {
  let msg = `${r.status} ${r.statusText}`;
  try {
    const j = await r.json();
    msg = j.error ?? (typeof j.detail === "string" ? j.detail : msg);
  } catch {
    /* not JSON: keep the status line */
  }
  throw new ApiError(msg);
}

async function json<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await call(method, url, body);
  if (!r.ok) await fail(r);
  return r.json();
}

/** A paid call: 409 is a result ({price} or {reason}), never a throw. */
async function paid(url: string, body?: unknown): Promise<Paid> {
  const r = await call("POST", url, body);
  if (r.status === 409) {
    const j = await r.json();
    return { ok: false, ...(j.price !== undefined ? { price: j.price } : {}), ...(j.reason !== undefined ? { reason: j.reason } : {}) };
  }
  if (!r.ok) await fail(r);
  return { ok: true, job: (await r.json()).job };
}

export const listProjects = () => json<{ id: string; song: string; kinds: Kind[] }[]>("GET", "/api/projects");
export const createProject = (f: { name: string; song: File; source: string; kinds: Kind[] }) => {
  const d = new FormData();
  d.append("name", f.name);
  d.append("song", f.song);
  d.append("source", f.source);
  d.append("kinds", f.kinds.join(","));
  return json<{ id: string }>("POST", "/api/projects", d);
};
export const getState = (pid: string) => json<State>("GET", `${base(pid)}/state`);

/** 409 means decided shots would reopen: returns {ok:false}; send again with reset_shots to confirm. */
export async function patchProject(
  pid: string,
  body: { range?: [number | null, number | null]; kinds?: Kind[]; seed?: string; vision_flags?: boolean; reset_shots?: boolean },
): Promise<{ ok: true } | { ok: false; reason: string }> {
  const r = await call("PATCH", base(pid), body);
  if (r.status === 409) return { ok: false, reason: (await r.json()).detail ?? "decided shots would reopen" };
  if (!r.ok) await fail(r);
  return { ok: true };
}
export const putShotModes = (pid: string, shots: string[], step: string, mode: string) =>
  json<object>("PUT", `${base(pid)}/shot-modes`, { shots, step, mode });

export const putBrief = (pid: string, brief: Brief) => json<object>("PUT", `${base(pid)}/brief`, { brief });
export const suggestBrief = (pid: string, steer = "") => json<{ job: string }>("POST", `${base(pid)}/suggest/brief`, { steer });
export const useBrief = (pid: string, id: string) => json<object>("POST", `${base(pid)}/brief/use`, { id });

export const putShot = (pid: string, sid: string, shot: Shot) => json<object>("PUT", `${base(pid)}/shots/${sid}`, { shot });
export const suggestShots = (pid: string, shots: string[], steer: string, price: number) =>
  paid(`${base(pid)}/suggest/shots`, { shots, steer, price });
export const useShot = (pid: string, sid: string, id: string) => json<object>("POST", `${base(pid)}/shots/${sid}/use`, { id });

export const suggestKeyframes = (pid: string, sid: string, price: number, near_seed: number | null = null) =>
  paid(`${base(pid)}/suggest/keyframes/${sid}`, { price, near_seed });
export const useKeyframe = (pid: string, sid: string, id: string) =>
  json<object>("POST", `${base(pid)}/keyframes/${sid}/use`, { id });
export const noKeyframe = (pid: string, sid: string, note: string) => json<object>("POST", `${base(pid)}/keyframes/${sid}/none`, { note });
export const uploadKeyframe = (pid: string, sid: string, file: File) => {
  const d = new FormData();
  d.append("file", file);
  return json<object>("POST", `${base(pid)}/uploads/keyframe/${sid}`, d);
};

export const pickTake = (pid: string, sid: string, key: string, inPoint: number) =>
  json<object>("POST", `${base(pid)}/takes/${sid}/pick`, { key, in: inPoint });
/** With `shot` the edit is the rewrite and the answer is {}; without it a rewrite job is queued ({job}). */
export const rejectTakes = (pid: string, sid: string, reason: string, shot?: Shot) =>
  json<{ job?: string }>("POST", `${base(pid)}/takes/${sid}/reject`, shot ? { reason, shot } : { reason });
export const uploadTake = (pid: string, sid: string, file: File, inPoint: number) => {
  const d = new FormData();
  d.append("file", file);
  d.append("in", String(inPoint));
  return json<object>("POST", `${base(pid)}/uploads/take/${sid}`, d);
};

export const continueRun = (pid: string, price: number, only?: string[]) =>
  paid(`${base(pid)}/continue`, only ? { price, only } : { price });
export const assemble = (pid: string) => json<{ job: string }>("POST", `${base(pid)}/assemble`);

export const previewUrl = (pid: string, sid: string, candidate?: string) =>
  `${base(pid)}/preview?sid=${encodeURIComponent(sid)}${candidate ? `&candidate=${encodeURIComponent(candidate)}` : ""}`;
export const getFrames = (pid: string, start: number, end: number) =>
  json<unknown[]>("GET", `${base(pid)}/frames?start=${start}&end=${end}`);
