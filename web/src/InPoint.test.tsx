import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { InPoint } from "./InPoint";

afterEach(cleanup);

function drag(clipDuration: number, window: number, x: number, width = 100) {
  cleanup();
  const onChange = vi.fn();
  render(<InPoint clipDuration={clipDuration} window={window} value={0} onChange={onChange} />);
  const bar = screen.getByRole("slider");
  bar.getBoundingClientRect = () => ({ left: 0, width, top: 0, right: width, bottom: 10, height: 10, x: 0, y: 0, toJSON() {} });
  fireEvent.mouseDown(bar, { clientX: x });
  fireEvent.mouseUp(window_());
  return onChange.mock.calls.at(-1)?.[0];
}
const window_ = () => document;

test("in-point clamps to clip minus window", () => {
  expect(drag(4.8, 2.5, 500)).toBeCloseTo(2.3, 6);
  expect(drag(4.8, 2.5, -50)).toBe(0);
});

test("in-point snaps to frames", () => {
  expect(drag(10, 2, 5.1)).toBeCloseTo(0.5, 6);
});
