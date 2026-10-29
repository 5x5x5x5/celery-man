import { expect, test } from "vitest";
import { roundLabel } from "./steps/Takes";

test("counter comes from the landed round", () => {
  expect(roundLabel({ landed_round: 0, cap: null })).toBe("no rounds yet");
  expect(roundLabel({ landed_round: 3, cap: null })).toBe("round 3");
  expect(roundLabel({ landed_round: 2, cap: 2 })).toBe("round 2 of 2");
});
