import { useEffect, useState } from "react";
import { listProjects } from "./api";
import { ContinueButton } from "./PricedButton";
import { JobsBadge } from "./JobsBadge";
import { SpendMeter } from "./SpendMeter";
import { StepStrip } from "./StepStrip";
import { NewProject, Song } from "./steps/Song";
import { STEPS } from "./types";
import type { Step } from "./types";
import { useProject } from "./useProject";

type Route = { pid: null } | { pid: string; step: Step };

export function parseRoute(hash: string): Route {
  const m = /^#\/p\/([^/]+)(?:\/([^/]+))?$/.exec(hash);
  if (!m) return { pid: null };
  return { pid: decodeURIComponent(m[1]), step: (STEPS as readonly string[]).includes(m[2]) ? (m[2] as Step) : "song" };
}

function Projects() {
  const [list, setList] = useState<{ id: string; song: string }[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    listProjects().then(setList, (e) => setError(String(e.message ?? e)));
  }, []);
  return (
    <main>
      <h1>Projects</h1>
      {error && <p className="error">{error}</p>}
      <ul className="plain">
        {list?.map((p) => (
          <li key={p.id}><a href={`#/p/${p.id}/song`}>{p.id}</a> <span className="muted">{p.song}</span></li>
        ))}
      </ul>
      {list?.length === 0 && <p className="muted">No projects yet.</p>}
      {adding ? <NewProject onCreated={(pid) => (location.hash = `#/p/${pid}/song`)} /> : <button className="primary" onClick={() => setAdding(true)}>New</button>}
    </main>
  );
}

function ProjectView({ pid, step }: { pid: string; step: Step }) {
  const { state, error, refresh } = useProject(pid);
  return (
    <>
      <header className="bar">
        <a href="#/">Projects</a>
        <strong>{pid}</strong>
        <span className="grow" />
        {state && <ContinueButton pid={pid} state={state} onDone={refresh} />}
        {state && <SpendMeter spend={state.spend} />}
        {state && <JobsBadge jobs={state.jobs} />}
      </header>
      {state && <StepStrip pid={pid} here={step} steps={state.steps} />}
      <main>
        {error && <p className="error">{error}</p>}
        {!state && !error && <p className="muted">Loading…</p>}
        {state && step === "song" && <Song state={state} refresh={refresh} />}
        {state && step !== "song" && <p className="muted">The {step} step is not built yet.</p>}
      </main>
    </>
  );
}

export function App() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const r = parseRoute(hash);
  return r.pid === null ? <Projects /> : <ProjectView key={r.pid} pid={r.pid} step={r.step} />;
}
