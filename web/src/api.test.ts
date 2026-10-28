import { afterEach, expect, test, vi } from "vitest";
import { continueRun, patchProject } from "./api";
import { pollMs } from "./useProject";
import type { Job } from "./types";

const job: Job = { id: "j", name: "continue", started: 1 };

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}
afterEach(() => vi.unstubAllGlobals());

test("paid call maps 409 price", async () => {
  mockFetch(409, { price: 0.42 });
  expect(await continueRun("p", 0.1)).toEqual({ ok: false, price: 0.42 });
});
test("paid call maps 409 reserve reason", async () => {
  mockFetch(409, { reason: "too low" });
  expect(await continueRun("p", 0.1)).toEqual({ ok: false, reason: "too low" });
});
test("paid call returns the job on 202", async () => {
  mockFetch(202, { job: "abc" });
  expect(await continueRun("p", 0.1)).toEqual({ ok: true, job: "abc" });
});
test("errors throw their message", async () => {
  mockFetch(422, { error: "bad input" });
  await expect(continueRun("p", 0.1)).rejects.toThrow("bad input");
});
test("patch 409 asks for confirmation", async () => {
  mockFetch(409, { detail: "decided shots would reopen" });
  expect(await patchProject("p", { range: [0, 4] })).toEqual({ ok: false, reason: "decided shots would reopen" });
});
test("poll interval fast while jobs run", () => {
  expect(pollMs({ running: job, queued: [], free: [], errors: [] })).toBe(1500);
  expect(pollMs({ running: null, queued: [job], free: [], errors: [] })).toBe(1500);
  expect(pollMs({ running: null, queued: [], free: [job], errors: [] })).toBe(1500);
  expect(pollMs({ running: null, queued: [], free: [], errors: [] })).toBe(5000);
});
