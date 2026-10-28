import { expect, test } from "vitest";
import { itemAt, needsLook, withOverride } from "./roughcut";
import type { CutItem, State } from "./types";

const mk = (shot: string, start: number, end: number): CutItem => ({ shot, kind: "take", src: `${shot}.mp4`, text: "", in: 1, start, end });
const cut = [mk("a", 0, 2.5), mk("b", 2.5, 5)];

test("itemAt finds window and local time", () => {
  expect(itemAt(cut, 3.0)).toEqual({ item: cut[1], local: 0.5 });
});
test("itemAt past the end is null", () => {
  expect(itemAt(cut, 999)).toBeNull();
});
test("override swaps only its window", () => {
  const out = withOverride(cut, { shot: "b", item: { kind: "still", src: "x.png" } });
  expect(out[0]).toBe(cut[0]);
  expect(out[1]).toEqual({ ...cut[1], kind: "still", src: "x.png" });
  expect(withOverride(cut, null)).toBe(cut);
});

const sh = (id: string, kind: "footage" | "animation", extra: Record<string, unknown> = {}) => ({
  shot: { id, kind, scene: null, ...(extra.shot as object) },
  modes: { shots: "auto", keyframes: "auto", takes: "auto" },
  keyframe: null,
  takes: null,
  price: 0,
  ...extra,
  ...(extra.shot ? { shot: { id, kind, scene: null, ...(extra.shot as object) } } : {}),
});
const flagged = (cutItems: CutItem[], shots: unknown[]) => needsLook({ shots, cut: cutItems } as unknown as State).map((l) => `${l.shot}:${l.step}`);

test("needsLook covers its five sources", () => {
  const shots = [
    sh("s1", "footage", { takes: { pick: { quick: true }, changed_since_upload: false } }),
    sh("s2", "animation", { shot: { scene: { params: {}, note: "fallback: boom" } } }),
    sh("s3", "footage", { keyframe: { entry: { note: "accepted after a rejected first still", approved: true }, changed_since_upload: false } }),
    sh("s4", "footage", { takes: { pick: null, changed_since_upload: true } }),
    sh("s5", "footage", { keyframe: { entry: { approved: true }, changed_since_upload: false } }),
    sh("s6", "footage"),
    sh("s7", "animation"),
  ];
  const cutItems = [
    { shot: "s5", kind: "still", src: "a.png", text: "", in: 0, start: 0, end: 1 },
    { shot: "s6", kind: "still", src: "b.png", text: "", in: 0, start: 1, end: 2 },
    { shot: "s7", kind: "blank", src: null, text: "", in: 0, start: 2, end: 3 },
  ] as CutItem[];
  expect(flagged(cutItems, shots)).toEqual(["s1:takes", "s2:shots", "s3:keyframes", "s4:takes", "s5:takes", "s6:keyframes", "s7:shots"]);
});
