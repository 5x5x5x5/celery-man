import { useEffect, useState } from "react";
import { approveKeyframe, continueRun, fileUrl, noKeyframe, putShot, putShotModes, redoKeyframe, suggestKeyframes, uploadKeyframe, useKeyframe } from "../api";
import { playWindow } from "../clock";
import { apiMode, loadView, ModeSwitch, saveView } from "../ModeSwitch";
import type { View } from "../ModeSwitch";
import { PricedButton } from "../PricedButton";
import { ShotForm } from "../ShotForm";
import { ShotStrip } from "../ShotStrip";
import { tryProps, useTryInPlace } from "../TryInPlace";
import { Upload } from "../Upload";
import type { Shot, ShotState, State } from "../types";

interface Still {
  id: string;
  path: string;
  seed: number;
  prompt_sha: string;
  flag?: string | null;
}

const badge = (s?: ShotState) => {
  const k = s?.keyframe;
  if (!k || k.need !== "done") return "…";
  return k.entry?.uploaded ? "uploaded" : s!.modes.keyframes === "auto" ? "✓ auto" : "✓ you";
};

export function Keyframes({ state, refresh, initial }: { state: State; refresh: () => void; initial?: string | null }) {
  const { project: p, brief, prices } = state;
  const pid = p.id;
  const { setOverride } = useTryInPlace();
  const footage = state.shots.filter((s) => s.shot.kind === "footage");
  const [sel, setSel] = useState<string | null>(initial ?? null);
  const [views, setViews] = useState<Record<string, View>>({});
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  useEffect(() => setSel(initial ?? null), [initial]);

  const cur = footage.find((s) => s.shot.id === sel);
  const sid = cur?.shot.id ?? "";
  const vkey = `${sid}:keyframes`;
  const view: View = views[sid] ?? (cur?.modes.keyframes === "auto" ? "auto" : loadView(pid, vkey));
  const guard = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };
  const select = (id: string) => {
    const s = footage.find((x) => x.shot.id === id)!.shot;
    setSel(id);
    setEditing(false);
    setRejecting(null);
    setUploadError("");
    playWindow(s.start, s.end, true);
  };
  const setView = (v: View) =>
    guard(async () => {
      setViews((m) => ({ ...m, [sid]: v }));
      saveView(pid, vkey, v);
      await putShotModes(pid, [sid], "keyframes", apiMode(v));
    });

  if (!brief || !state.shots.length) return <p className="muted">Write the brief and the shots first.</p>;
  if (!footage.length) return <p className="muted">No footage shots need keyframes.</p>;

  const kf = cur?.keyframe;
  const stills = cur ? ((state.candidates.keyframes[sid] as { items?: Still[] } | undefined)?.items ?? []) : [];
  const suggest = (near: number | null) => (shown: number) => suggestKeyframes(pid, sid, shown, near).then((r) => (refresh(), r));
  const shot = cur?.shot;

  return (
    <section>
      <h2>Keyframes</h2>
      <ShotStrip shots={footage.map((s) => s.shot)} selected={sel} onSelect={select} badge={(s) => <span className="badge">{badge(footage.find((x) => x.shot.id === s.id))}</span>} />
      {error && <p className="error">{error}</p>}
      {cur && shot && kf && (
        <div>
          <h3>{sid} <span className="muted">{shot.start.toFixed(1)}s to {shot.end.toFixed(1)}s</span></h3>
          <ModeSwitch value={view} onChange={setView} />
          {kf.changed_since_upload && <p className="error" role="alert">The shot changed since this still was uploaded. Keep it or replace it.</p>}
          <p>
            <span className="muted">Prompt (from the shot): </span>
            {kf.prompt_error ? <span className="error">{kf.prompt_error}</span> : <span>{kf.prompt}</span>}{" "}
            <button onClick={() => setEditing(!editing)}>Edit shot</button>
          </p>
          {editing && (
            <ShotForm
              key={sid}
              pid={pid}
              shot={shot}
              brief={brief}
              kinds={p.kinds}
              templates={{}}
              onSave={async (s: Shot, c) => {
                await putShot(pid, s.id, s, c);
                setEditing(false);
                refresh();
              }}
            />
          )}
          {kf.entry && <img src={fileUrl(pid, kf.entry.path)} alt="current still" style={{ maxWidth: "24rem", width: "100%" }} />}
          {kf.need === "judge" && (
            <div className="row">
              <button className="primary" onClick={() => guard(() => approveKeyframe(pid, sid))}>Approve</button>
              <button onClick={() => guard(() => redoKeyframe(pid, sid))}>Redo</button>
            </div>
          )}
          {view === "auto" && <p className="muted">{typeof kf.entry?.note === "string" ? kf.entry.note : "The model will pick this still when you continue."}</p>}
          {view === "suggestions" && (
            <>
              <div className="bulk">
                <PricedButton label="4 stills" price={prices.keyframe_set} onConfirm={suggest(null)} />
                <button onClick={() => setRejecting("")}>None of these</button>
              </div>
              {rejecting !== null && (
                <div className="row">
                  <input type="text" aria-label="What is wrong with these" value={rejecting} onChange={(e) => setRejecting(e.target.value)} />
                  <button
                    className="primary"
                    disabled={!rejecting.trim()}
                    onClick={() =>
                      guard(async () => {
                        await noKeyframe(pid, sid, rejecting);
                        location.hash = `#/p/${pid}/shots?shot=${sid}`;
                      })
                    }
                  >
                    Send note and edit the shot
                  </button>
                </div>
              )}
              <div className="cards stills">
                {stills.map((s) => (
                  <article className="card" key={s.id} {...tryProps(setOverride, { shot: sid, item: { kind: "still", src: s.path } })}>
                    <img src={fileUrl(pid, s.path)} alt="" />
                    {s.flag && <span className="badge error">{s.flag}</span>}
                    <div className="row">
                      <button className="primary" onClick={() => guard(() => useKeyframe(pid, sid, s.id))}>Approve</button>
                      <PricedButton label="Variations" price={prices.keyframe_set} onConfirm={suggest(s.seed)} />
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          {view === "manual" && (
            <div>
              <Upload
                accept="image/*"
                error={uploadError}
                onFile={(f) => {
                  setUploadError("");
                  uploadKeyframe(pid, sid, f).then(refresh, (e) => setUploadError(String(e.message ?? e)));
                }}
              />
              <PricedButton
                label="Generate one"
                price={cur.price}
                onConfirm={async (shown) => {
                  const r = await continueRun(pid, shown, [sid]);
                  refresh();
                  return r;
                }}
              />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
