import type { CutItem, ShotState, State, Step } from "./types";

/** A suggestion tried in place of one shot's window; `candidate` is an animation candidate id. */
export interface Override {
  shot: string;
  item: Partial<CutItem>;
  candidate?: string;
}

/** The cut item playing at song time t, and the seconds into its window. */
export function itemAt(cut: CutItem[], t: number): { item: CutItem; local: number } | null {
  const item = cut.find((c) => t >= c.start && t < c.end);
  return item ? { item, local: t - item.start } : null;
}

/** The cut with one shot's item changed; every other item is the same object. */
export const withOverride = (cut: CutItem[], o: Pick<Override, "shot" | "item"> | null): CutItem[] =>
  o ? cut.map((c) => (c.shot === o.shot ? { ...c, ...o.item } : c)) : cut;

export interface Look {
  shot: string;
  step: Step;
  why: string;
}

/** Everything the next assemble would use that a person may want to look at again. */
export function needsLook(state: State): Look[] {
  const out: Look[] = [];
  const own = (s: ShotState): Step => (s.shot.kind === "animation" ? "shots" : "takes");
  for (const s of state.shots) {
    const id = s.shot.id;
    if (s.takes?.pick?.quick) out.push({ shot: id, step: "takes", why: "quick fallback take" });
    if (s.shot.scene?.note?.startsWith("fallback:")) out.push({ shot: id, step: "shots", why: s.shot.scene.note });
    const note = s.keyframe?.entry?.note;
    if (typeof note === "string" && note.startsWith("accepted after")) out.push({ shot: id, step: "keyframes", why: note });
    if (s.keyframe?.changed_since_upload) out.push({ shot: id, step: "keyframes", why: "changed since the still was uploaded" });
    if (s.takes?.changed_since_upload) out.push({ shot: id, step: "takes", why: "changed since the clip was uploaded" });
  }
  for (const c of state.cut) {
    if (c.kind === "still" || c.kind === "card" || c.kind === "blank") {
      const s = state.shots.find((x) => x.shot.id === c.shot);
      // a still on a footage shot with no approved keyframe is that shot's missing keyframe, not a missing take
      const step: Step = !s ? "shots" : c.kind === "still" && s.shot.kind === "footage" && s.keyframe?.entry?.approved !== true ? "keyframes" : own(s);
      out.push({ shot: c.shot, step, why: `plays as a ${c.kind}` });
    }
  }
  return out;
}

