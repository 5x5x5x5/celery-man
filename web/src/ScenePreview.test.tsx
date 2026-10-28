import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ScenePreview } from "./ScenePreview";

afterEach(() => vi.unstubAllGlobals());
const post = (source: unknown, message: string) =>
  act(() => void window.dispatchEvent(new MessageEvent("message", { data: { type: "error", message }, source: source as Window })));

test("preview iframe is sandboxed without same-origin", () => {
  const { container } = render(<ScenePreview pid="p" sid="s02" start={0} end={2.5} />);
  expect(container.querySelector("iframe")!.getAttribute("sandbox")).toBe("allow-scripts");
});

test("only the iframe's own errors are shown, and as text", () => {
  const { container } = render(<ScenePreview pid="p" start={0} end={2.5} />);
  post(window, "from elsewhere");
  expect(screen.queryByText("from elsewhere")).toBeNull();
  post(container.querySelector("iframe")!.contentWindow, "<b>boom</b>");
  expect(screen.getByText("<b>boom</b>")).toBeTruthy();
  expect(container.querySelector("b")).toBeNull();
});
afterEach(cleanup);

test("frames wait for the source-matched ready message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => (url.includes("/frames") ? new Response(JSON.stringify([{ song_t: 0 }])) : new Response("<html></html>"))),
  );
  const { container } = render(<ScenePreview pid="p" sid="s02" start={0} end={2.5} />);
  const win = container.querySelector("iframe")!.contentWindow!;
  const spy = vi.spyOn(win, "postMessage");
  await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
  fireEvent.load(container.querySelector("iframe")!);
  const types = () => spy.mock.calls.map((c) => (c[0] as { type: string }).type);
  expect(types()).toEqual(["init"]);
  act(() => void window.dispatchEvent(new MessageEvent("message", { data: { type: "ready" }, source: window })));
  expect(types()).toEqual(["init"]);
  act(() => void window.dispatchEvent(new MessageEvent("message", { data: { type: "ready" }, source: win })));
  expect(types()).toContain("frame");
});
