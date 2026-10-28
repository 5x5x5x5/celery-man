import { useState } from "react";
import { assemble, fileUrl } from "../api";
import { Player } from "../RoughCut";
import { needsLook } from "../roughcut";
import type { ShotState, State } from "../types";

const STEP_MODES = ["shots", "keyframes", "takes"] as const;

const Dots = ({ s }: { s?: ShotState }) =>
  s && (
    <span className="dots" aria-label="who decided">
      {STEP_MODES.map((m) => <i key={m} className={s.modes[m]} title={`${m}: ${s.modes[m] === "auto" ? "model" : "you"}`} />)}
      {s.takes?.pick?.quick && <b title="quick fallback">q</b>}
      {s.takes?.pick?.uploaded && <b title="uploaded">u</b>}
    </span>
  );

export function Cut({ state, refresh }: { state: State; refresh: () => void }) {
  const pid = state.project.id;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const byId = (id: string) => state.shots.find((s) => s.shot.id === id);
  const looks = needsLook(state);
  const modes = state.shots.flatMap((s) => STEP_MODES.map((m) => s.modes[m]));
  const picks = state.shots.flatMap((s) => (s.takes?.pick ? [s.takes.pick] : []));
  const { spent, cap } = state.spend;

  return (
    <section className="bigplayer">
      <h2>Cut</h2>
      {state.cut.length ? <Player state={state} decided={(id) => <Dots s={byId(id)} />} /> : <p className="muted">Nothing to show yet.</p>}
      <p>
        {modes.filter((m) => m === "auto").length} decided by the model, {modes.filter((m) => m !== "auto").length} by you,{" "}
        {picks.filter((p) => p.quick).length} quick, {picks.filter((p) => p.uploaded).length} uploaded. Spent ${spent.toFixed(2)} of ${cap.toFixed(2)}.
      </p>
      <div className="row">
        <button
          className="primary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await assemble(pid);
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            }
            setBusy(false);
            refresh();
          }}
        >
          Assemble draft
        </button>
        {state.steps.some((s) => s.step === "assemble" && s.state !== "missing") && <a href={fileUrl(pid, "out/video.mp4")} download>Download draft</a>}
        <a href={fileUrl(pid, "out/report.md")} target="_blank" rel="noreferrer">Report</a>
        <a href={fileUrl(pid, "PROVENANCE.md")} target="_blank" rel="noreferrer">Provenance</a>
      </div>
      {error && <p className="error">{error}</p>}
      <h3>Needs a look</h3>
      {looks.length === 0 && <p className="muted">Nothing flagged.</p>}
      <ul className="plain">
        {looks.map((l, i) => (
          <li key={i}>
            <a href={`#/p/${pid}/${l.step}?shot=${l.shot}`}>{l.shot}</a> <span className="muted">{l.why}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
