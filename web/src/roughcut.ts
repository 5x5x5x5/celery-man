import type { CutItem } from "./types";

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
