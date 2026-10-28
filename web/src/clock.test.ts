import { expect, test } from "vitest";
import { frameIndex } from "./clock";

test("frameIndex clamps to the window", () => {
  expect(frameIndex(1.0, 0.5, 24)).toBe(12);
  expect(frameIndex(0.1, 0.5, 24)).toBe(0);
});
