import { useEffect, useRef, useState } from "react";
import { continueRun, fileUrl, pickTake, putShotModes, rejectTakes, uploadTake } from "../api";
import { playWindow, useSongTime } from "../clock";
import { InPoint } from "../InPoint";
import { apiMode, loadView, ModeSwitch, saveView } from "../ModeSwitch";
import type { View } from "../ModeSwitch";
import type { Override } from "../roughcut";
import { tryProps, useTryInPlace } from "../TryInPlace";
import { PricedButton } from "../PricedButton";
import { ShotForm } from "../ShotForm";
import { ShotStrip } from "../ShotStrip";
import { Upload } from "../Upload";
import type { ShotState, ShotTake, State } from "../types";

/** Shows the clip at in + (t - start) while the song plays inside the shot's window, so it stays on the song. */
export function TakeCard({ src, start, window: win, inPoint, onIn, tryout, children }: {
  src: string;
  tryout?: Override;
  start: number;
  window: number;
  inPoint: number;
  onIn: (v: number) => void;
  children: React.ReactNode;
}) {
  const v = useRef<HTMLVideoElement>(null);
  const [dur, setDur] = useState(0);
  const t = useSongTime();
  const { setOverride } = useTryInPlace();
  useEffect(() => {
    if (v.current && dur) v.current.currentTime = inPoint + (t >= start && t < start + win ? t - start : 0);
  }, [t, inPoint, dur, start, win]);
  return (
    <article className="card takecard" {...tryProps(setOverride, tryout ?? null)}>
      <video ref={v} src={src} muted preload="auto" onLoadedMetadata={(e) => setDur(e.currentTarget.duration)} />
      {dur > 0 && <InPoint clipDuration={dur} window={win} value={inPoint} onChange={onIn} />}
      {children}
    </article>
  );
}

export const roundLabel = (t: { landed_round: number; cap: number | null }): string =>
  t.landed_round === 0 ? "no rounds yet" : t.cap === null ? `round ${t.landed_round}` : `round ${t.landed_round} of ${t.cap}`;

const running = (s: ShotState, busy: boolean, names: string[]) => busy && (s.takes?.need === "run" || names.some((n) => n.includes(s.shot.id)));

export function Takes({ state, refresh, initial }: { state: State; refresh: () => void; initial?: string | null }) {
  const { project: p, brief, prices, jobs } = state;
  const pid = p.id;
  const footage = state.shots.filter((s) => s.shot.kind === "footage");
  const [sel, setSel] = useState<string | null>(initial ?? null);
  const [views, setViews] = useState<Record<string, View>>({});
  const [ins, setIns] = useState<Record<string, number>>({});
  const [reason, setReason] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [uploadIn, setUploadIn] = useState(0);
  useEffect(() => setSel(initial ?? null), [initial]);

  const all = [...(jobs.running ? [jobs.running] : []), ...jobs.queued, ...jobs.free];
  const names = all.map((j) => j.name);
  const cur = footage.find((s) => s.shot.id === sel);
  const sid = cur?.shot.id ?? "";
  const vkey = `${sid}:takes`;
  const view: View = views[sid] ?? (cur?.modes.takes === "auto" ? "auto" : loadView(pid, vkey));
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
    setReason(null);
    setEditing(false);
    setUploadError("");
    playWindow(s.start, s.end, true);
  };
  const setView = (v: View) =>
    guard(async () => {
      setViews((m) => ({ ...m, [sid]: v }));
      saveView(pid, vkey, v);
      await putShotModes(pid, [sid], "takes", apiMode(v));
    });
  const badge = (s: ShotState) => {
    if (running(s, all.length > 0, names)) return "⟳";
    const t = s.takes;
    if (!t || t.need !== "done") return "…";
    return t.pick?.uploaded ? "uploaded" : s.modes.takes === "auto" ? "✓ auto" : "✓ you";
  };

  if (!brief || !state.shots.length) return <p className="muted">Write the brief and the shots first.</p>;
  if (!footage.length) return <p className="muted">No footage shots need takes.</p>;

  const t = cur?.takes;
  const win = cur ? cur.shot.end - cur.shot.start : 0;
  const counter = t ? roundLabel(t) : "";
  const nextRound = (
    <PricedButton
      label="Next round"
      price={prices.take_round}
      disabledReason={t?.need === "limit" ? "round cap reached" : undefined}
      onConfirm={async (shown) => {
        const r = await continueRun(pid, shown, [sid]);
        refresh();
        return r;
      }}
    />
  );

  return (
    <section>
      <h2>Takes</h2>
      <ShotStrip shots={footage.map((s) => s.shot)} selected={sel} onSelect={select} badge={(s) => <span className="badge">{badge(footage.find((x) => x.shot.id === s.id)!)}</span>} />
      {error && <p className="error">{error}</p>}
      {cur && t && (
        <div>
          <h3>{sid} <span className="muted">{counter}</span></h3>
          <p>
            <a href={`#/p/${pid}/keyframes?shot=${sid}`}>Back to keyframe</a> · <a href={`#/p/${pid}/shots?shot=${sid}`}>Back to shot</a>
          </p>
          <ModeSwitch value={view} onChange={setView} />
          {t.changed_since_upload && <p className="error" role="alert">The shot changed since this clip was uploaded. Keep it or replace it.</p>}
          {view === "auto" && <p className="muted">The model will pick a take when you continue.</p>}
          {view === "suggestions" && (
            <>
              <div className="cards">
                {t.takes.map((k: ShotTake) => (
                  <TakeCard key={k.key} src={fileUrl(pid, k.clip)} start={cur.shot.start} window={win} inPoint={ins[k.key] ?? 0} tryout={{ shot: sid, item: { kind: "take", src: k.clip, in: ins[k.key] ?? 0 } }} onIn={(v) => setIns((m) => ({ ...m, [k.key]: v }))}>
                    <div className="row">
                      <button className="primary" onClick={() => guard(() => pickTake(pid, sid, k.key, ins[k.key] ?? 0))}>
                        {t.pick?.key === k.key ? "Chosen" : "Use"}
                      </button>
                    </div>
                  </TakeCard>
                ))}
              </div>
              {t.takes.length === 0 && <p className="muted">No takes yet.</p>}
              <div className="bulk">
                <button onClick={() => setReason("")}>None of these</button>
                {nextRound}
              </div>
              {reason !== null && (
                <div>
                  <label className="field">
                    <span>What is wrong with these</span>
                    <input type="text" value={reason} onChange={(e) => setReason(e.target.value)} />
                  </label>
                  <div className="row">
                    <button disabled={!reason.trim()} onClick={() => guard(async () => (await rejectTakes(pid, sid, reason), setReason(null)))}>
                      Send the reason
                    </button>
                    <button onClick={() => setEditing(!editing)}>Edit shot too</button>
                  </div>
                  {editing && (
                    <ShotForm
                      key={sid}
                      pid={pid}
                      shot={cur.shot}
                      brief={brief}
                      kinds={p.kinds}
                      templates={{}}
                      onSave={async (s) => {
                        await rejectTakes(pid, sid, reason, s);
                        setReason(null);
                        setEditing(false);
                        refresh();
                      }}
                    />
                  )}
                </div>
              )}
            </>
          )}
          {view === "manual" && (
            <div>
              <label className="field">
                <span>In point (seconds)</span>
                <input type="number" min={0} step={1 / 24} value={uploadIn} onChange={(e) => setUploadIn(Math.max(0, Number(e.target.value)))} />
              </label>
              <Upload
                accept="video/*"
                error={uploadError}
                onFile={(f) => {
                  setUploadError("");
                  uploadTake(pid, sid, f, uploadIn).then(refresh, (e) => setUploadError(String(e.message ?? e)));
                }}
              />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
