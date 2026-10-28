import { useCallback, useEffect, useRef, useState } from "react";
import { getState } from "./api";
import type { Jobs, State } from "./types";

export const FAST_MS = 1500;
export const SLOW_MS = 5000;

export const pollMs = (j: Jobs): number => (j.running || j.queued.length || j.free.length ? FAST_MS : SLOW_MS);

/** Polls the project's State: fast while any job runs. Call refresh() right after any mutation. */
export function useProject(pid: string): { state: State | null; error: string; refresh: () => void } {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((n) => n + 1), []);
  const latest = useRef(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const seq = ++latest.current;
    const load = async () => {
      let next = SLOW_MS;
      try {
        const s = await getState(pid);
        if (!alive || seq !== latest.current) return;
        setState(s);
        setError("");
        next = pollMs(s.jobs);
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
      }
      if (alive) timer = setTimeout(load, next);
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [pid, tick]);

  return { state, error, refresh };
}
