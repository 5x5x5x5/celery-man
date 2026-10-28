import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("the project's song is on the shared audio even when no rough cut player is mounted", async () => {
  const state = {
    project: { id: "p", song: "song.mp3", modes: {}, kinds: [] },
    timing: null, brief: null, shots: [], steps: [], cut: [],
    spend: { spent: 0, reserve: 0, cap: 1 }, prices: { continue: 0, still: 0, keyframe_set: 0, take_round: 0 }, candidates: {},
    jobs: { running: null, queued: [], free: [], errors: [] },
  };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(state))));
  location.hash = "#/p/p/shots";
  const srcs: string[] = [];
  Object.defineProperty(HTMLMediaElement.prototype, "src", { configurable: true, get: () => "", set: (v: string) => void srcs.push(v) });
  render(<App />);
  await screen.findByText("No cut yet.");
  expect(srcs).toEqual(["/api/projects/p/files/song.mp3"]);
});
