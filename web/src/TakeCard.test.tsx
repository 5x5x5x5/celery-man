import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

let t = 0;
const listeners = new Set<() => void>();
vi.mock("./clock", () => ({
  useSongTime: () => {
    const [, n] = useState(0);
    useEffect(() => {
      const f = () => n((x) => x + 1);
      listeners.add(f);
      return () => void listeners.delete(f);
    }, []);
    return t;
  },
  playWindow: () => {},
}));
import { TakeCard } from "./steps/Takes";

afterEach(cleanup);

test("clip time follows in + (t - start) on the song clock", () => {
  t = 12;
  const { container } = render(<TakeCard src="x.mp4" start={10} window={2.5} inPoint={1} onIn={() => {}}>{null}</TakeCard>);
  const v = container.querySelector("video")!;
  Object.defineProperty(v, "duration", { value: 4.8 });
  fireEvent.loadedMetadata(v);
  expect(v.currentTime).toBeCloseTo(3);
  t = 11;
  act(() => listeners.forEach((f) => f()));
  expect(v.currentTime).toBeCloseTo(2);
  t = 20; // song outside the window: the clip rests at its in-point
  act(() => listeners.forEach((f) => f()));
  expect(v.currentTime).toBeCloseTo(1);
});
