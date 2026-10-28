import { expect, test } from "vitest";
import { loadView, saveView } from "./ModeSwitch";

test("the saved view is per project and shot", () => {
  saveView("a", "s01", "manual");
  expect(loadView("a", "s01")).toBe("manual");
  expect(loadView("b", "s01")).toBe("suggestions");
});
