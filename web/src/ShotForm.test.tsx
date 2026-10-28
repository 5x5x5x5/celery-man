import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ShotForm } from "./ShotForm";
import type { Brief, Shot } from "./types";

afterEach(cleanup);
const brief: Brief = {
  logline: "l",
  style: { medium: "m", palette: "p", lighting: "l", camera: "c" },
  cast: [{ name: "A", description: "d" }],
  places: [{ name: "P", description: "d" }],
  avoid: [],
  look: { background: "#000000", colors: ["#ffffff"], typeface: "sans" },
};
const shot: Shot = { id: "s01", start: 0, end: 2, section: "x", action: "a person walks", cast: ["A"], place: null, framing: "wide", kind: "footage", scene: null };

test("kind choice limited to project kinds", () => {
  render(<ShotForm pid="p" shot={{ ...shot, kind: "animation", scene: { template: "bars", params: {} } }} brief={brief} kinds={["animation"]} templates={{}} onSave={async () => {}} />);
  expect(screen.queryByLabelText("footage")).toBeNull();
  expect(screen.getByLabelText("animation")).toBeTruthy();
});

test("server 422 shown inline", async () => {
  const onSave = vi.fn().mockRejectedValue(new Error("action is too long"));
  render(<ShotForm pid="p" shot={shot} brief={brief} kinds={["footage"]} templates={{}} onSave={onSave} />);
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(screen.getByText("action is too long")).toBeTruthy());
});

const templates = { bars: { family: "f", description: "d", params: { count: { type: "integer" as const, default: 16, min: 3, max: 64 } } } };

test("open shot with only animation gets a template to choose", () => {
  render(<ShotForm pid="p" shot={{ ...shot, kind: "open" }} brief={brief} kinds={["animation"]} templates={templates} onSave={async () => {}} />);
  expect(screen.getByLabelText("Scene template")).toBeTruthy();
});

test("typed code is previewed unsaved and sent on Save", async () => {
  const fetchMock = vi.fn(async (_u: string, _i?: RequestInit) => new Response("<html></html>"));
  vi.stubGlobal("fetch", fetchMock);
  const onSave = vi.fn().mockResolvedValue(undefined);
  const anim: Shot = { ...shot, kind: "animation", scene: { file: "scenes/a.js", params: {} } };
  render(<ShotForm pid="p" shot={anim} brief={brief} kinds={["animation"]} templates={templates} code="" onSave={onSave} />);
  fireEvent.change(screen.getByLabelText("Scene code"), { target: { value: "draw()" } });
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/preview") && (i as RequestInit)?.method === "POST" && String((i as RequestInit).body).includes("draw()"))).toBe(true),
  );
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(onSave).toHaveBeenCalledWith(expect.anything(), "draw()"));
  vi.unstubAllGlobals();
});
