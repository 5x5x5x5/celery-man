import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
vi.mock("./clock", () => ({ playWindow: () => {}, useSongTime: () => 0 }));
import { Keyframes } from "./steps/Keyframes";
import { Takes } from "./steps/Takes";
import type { State } from "./types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

const shot = {
  id: "s01",
  start: 0,
  end: 2,
  section: "1",
  kind: "footage",
  action: "a person walks",
  cast: ["A"],
  place: "P",
  framing: "wide shot",
  scene: null,
};
const state = (need: "gen" | "judge" | "done") =>
  ({
    project: { id: "p", kinds: ["footage"], modes: {} },
    brief: {
      logline: "a person walks",
      style: {},
      cast: [],
      places: [],
      avoid: [],
      look: { background: "", colors: [], typeface: "" },
    },
    shots: [
      {
        shot,
        open: false,
        modes: {
          shots: "tastemaker",
          keyframes: "tastemaker",
          takes: "tastemaker",
        },
        render: null,
        price: 0.44,
        keyframe: {
          need,
          entry: { path: "keys/s01.png", approved: null, prompt_sha: "x" },
          changed_since_upload: false,
          prompt: "a person walks",
          prompt_error: "",
        },
        takes: {
          need: "run",
          round: 2,
          landed_round: 1,
          cap: null,
          takes: [],
          pick: null,
          changed_since_upload: false,
        },
      },
    ],
    prices: { continue: 0.9, still: 0.01, keyframe_set: 0.04, take_round: 0.3 },
    candidates: { brief: null, shots: {}, keyframes: {} },
    jobs: { running: null, queued: [], free: [], errors: [] },
  }) as unknown as State;

test("Generate one shows the shot's own price, not a single still's", () => {
  localStorage.setItem("view:p:s01:keyframes", "manual");
  render(<Keyframes state={state("gen")} refresh={() => {}} initial="s01" />);
  expect(screen.getByText("Generate one · $0.44")).toBeTruthy();
});

test("Next round shows the shot's own price, not a whole round's", () => {
  render(<Takes state={state("done")} refresh={() => {}} initial="s01" />);
  expect(screen.getByText("Next round · $0.44")).toBeTruthy();
});

test("a still awaiting approval has Approve and Redo beside it", async () => {
  const fetch = vi.fn(async () => new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  render(<Keyframes state={state("judge")} refresh={() => {}} initial="s01" />);
  fireEvent.click(screen.getAllByText("Approve")[0]);
  fireEvent.click(screen.getByText("Redo"));
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  const urls = fetch.mock.calls.map((c) => String((c as unknown[])[0]));
  expect(urls).toEqual([
    "/api/projects/p/keyframes/s01/approve",
    "/api/projects/p/keyframes/s01/redo",
  ]);
});

test("no Approve beside a still that is already done", () => {
  render(<Keyframes state={state("done")} refresh={() => {}} initial="s01" />);
  expect(screen.queryByText("Redo")).toBeNull();
});
