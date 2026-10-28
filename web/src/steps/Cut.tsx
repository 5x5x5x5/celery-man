import { useState } from "react";
import { assemble, fileUrl } from "../api";
import { Player } from "../RoughCut";
import type { ShotState, State, Step } from "../types";

interface Look {
  shot: string;
  step: Step;
  why: string;
}

const STEP_MODES = ["shots", "keyframes", "takes"] as const;

/** Everything the next assemble would use that a person may want to look at again. */
export function needsLook(state: State): Look[] {
  const out: Look[] = [];
  const own = (s: ShotState): Step => (s.shot.kind === "animation" ? "shots" : "takes");
  for (const s of state.shots) {
    const id = s.shot.id;
    if (s.takes?.pick?.quick) out.push({ shot: id, step: "takes", why: "quick fallback take" });
    if (s.shot.scene?.note?.startsWith("fallback:")) out.push({ shot: id, step: "shots", why: s.shot.scene.note });
    const note = s.keyframe?.entry?.note;
    if (typeof note === "string" && note.startsWith("accepted after")) out.push({ shot: id, step: "keyframes", why: note });
    if (s.keyframe?.changed_since_upload) out.push({ shot: id, step: "keyframes", why: "changed since the still was uploaded" });
    if (s.takes?.changed_since_upload) out.push({ shot: id, step: "takes", why: "changed since the clip was uploaded" });
  }
  for (const c of state.cut) {
    if (c.kind === "still" || c.kind === "card" || c.kind === "blank") {
      const s = state.shots.find((x) => x.shot.id === c.shot);
      out.push({ shot: c.shot, step: s ? own(s) : "shots", why: `plays as a ${c.kind}` });
    }
  }
  return out;
}

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
        <a href={fileUrl(pid, "out/video.mp4")} download>Download draft</a>
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
