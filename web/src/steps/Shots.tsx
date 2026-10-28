import { useEffect, useMemo, useState } from "react";
import { continueRun, fileUrl, getTemplates, putShot, putShotModes, suggestShots, useShot } from "../api";
import { playWindow } from "../clock";
import { apiMode, loadView, ModeSwitch, saveView } from "../ModeSwitch";
import type { View } from "../ModeSwitch";
import { PricedButton } from "../PricedButton";
import { ScenePreview } from "../ScenePreview";
import { ShotForm } from "../ShotForm";
import { ShotStrip } from "../ShotStrip";
import { Suggestions } from "../Suggestions";
import { Waveform } from "../Waveform";
import type { Shot, ShotIdea, State, Templates } from "../types";

const IDEAS = 3;

export function Shots({ state, refresh }: { state: State; refresh: () => void }) {
  const { project: p, timing, brief, shots, prices } = state;
  const pid = p.id;
  const [sel, setSel] = useState<string | null>(null);
  const [views, setViews] = useState<Record<string, View>>({});
  const [steer, setSteer] = useState("");
  const [draft, setDraft] = useState<{ shot: Shot; code?: string } | null>(null);
  const [templates, setTemplates] = useState<Templates>({});
  const [error, setError] = useState("");
  useEffect(() => {
    getTemplates().then(setTemplates, (e) => setError(String(e.message ?? e)));
  }, []);

  const cur = shots.find((s) => s.shot.id === sel);
  const open = shots.filter((s) => s.shot.kind === "open").map((s) => s.shot.id);
  const viewOf = (sid: string): View => {
    const s = shots.find((x) => x.shot.id === sid);
    return views[sid] ?? (s?.modes.shots === "auto" ? "auto" : loadView(pid, sid));
  };
  const badge = useMemo(
    () => (s: Shot) => {
      const st = shots.find((x) => x.shot.id === s.id);
      return <span className="badge">{s.kind === "open" ? "open" : st?.modes.shots === "auto" ? "auto" : "you"}</span>;
    },
    [shots],
  );

  const guard = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };
  const pick = (sid: string) => {
    const s = shots.find((x) => x.shot.id === sid)!.shot;
    setSel(sid);
    setDraft(null);
    setSteer("");
    playWindow(s.start, s.end, true);
  };
  const setView = (sid: string, v: View) =>
    guard(async () => {
      setViews((m) => ({ ...m, [sid]: v }));
      saveView(pid, sid, v);
      await putShotModes(pid, [sid], "shots", apiMode(v));
    });

  if (!timing || !brief) return <p className="muted">Pick the part of the song on the Song step and write the brief first.</p>;

  const ideas = cur ? ((state.candidates.shots[cur.shot.id] as { items?: ShotIdea[] } | undefined)?.items ?? []) : [];
  const view = cur ? viewOf(cur.shot.id) : "manual";
  const price = (n: number) => prices.still * IDEAS * n;
  const suggest = (sids: string[]) => (shown: number) => suggestShots(pid, sids, steer, shown).then((r) => (refresh(), r));

  return (
    <section>
      <h2>Shots</h2>
      <Waveform src={fileUrl(pid, p.song)} duration={timing.duration} windows={timing.windows} range={p.range} />
      <ShotStrip shots={shots.map((s) => s.shot)} selected={sel} onSelect={pick} badge={badge} />
      <div className="bulk">
        <PricedButton label="Suggest for all open shots" price={price(open.length)} disabledReason={open.length ? undefined : "no open shots"} onConfirm={suggest(open)} />
        <PricedButton
          label="Auto the rest"
          price={prices.continue}
          disabledReason={open.length ? undefined : "no open shots"}
          onConfirm={async (shown) => {
            await putShotModes(pid, open, "shots", "auto");
            const r = await continueRun(pid, shown);
            refresh();
            return r;
          }}
        />
      </div>
      {error && <p className="error">{error}</p>}
      {cur && (
        <div>
          <h3>{cur.shot.id} <span className="muted">{cur.shot.start.toFixed(1)}s to {cur.shot.end.toFixed(1)}s</span></h3>
          <ModeSwitch value={view} onChange={(v) => setView(cur.shot.id, v)} />
          {view === "auto" && <p className="muted">The model will decide this shot when you continue.</p>}
          {view === "suggestions" && (
            <>
              <PricedButton label="Suggest for this shot" price={price(1)} onConfirm={suggest([cur.shot.id])} />
              <Suggestions
                items={ideas.filter((i) => i.id)}
                steer={steer}
                onSteer={setSteer}
                onUse={(id) => guard(() => useShot(pid, cur.shot.id, id))}
                onEdit={(id) => {
                  const it = ideas.find((i) => i.id === id)!;
                  setDraft({ shot: it.shot, code: it.code });
                  setView(cur.shot.id, "manual");
                }}
                render={(it) => (
                  <>
                    <p>{it.shot.kind === "animation" ? it.shot.scene?.template ?? "own code" : it.shot.action}</p>
                    {it.still && <img src={fileUrl(pid, it.still)} alt="" style={{ width: "100%" }} />}
                    {it.shot.kind === "animation" && <ScenePreview pid={pid} sid={cur.shot.id} candidate={it.id} start={cur.shot.start} end={cur.shot.end} />}
                    {it.error && <p className="error">{it.error}</p>}
                  </>
                )}
              />
            </>
          )}
          {view === "manual" && (
            <ShotForm
              key={cur.shot.id + JSON.stringify(draft)}
              pid={pid}
              shot={draft?.shot ?? cur.shot}
              code={draft?.code}
              brief={brief}
              kinds={p.kinds}
              templates={templates}
              onSave={async (s) => {
                await putShot(pid, s.id, s);
                setDraft(null);
                refresh();
              }}
            />
          )}
        </div>
      )}
    </section>
  );
}
