import { expect, test } from "vitest";
import { itemAt, withOverride } from "./roughcut";
import type { CutItem } from "./types";

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
