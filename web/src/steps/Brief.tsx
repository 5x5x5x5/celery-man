import { useState } from "react";
import { continueRun, patchProject, putBrief, putShotModes, suggestBrief, useBrief } from "../api";
import { apiMode, loadView, ModeSwitch, saveView } from "../ModeSwitch";
import type { View } from "../ModeSwitch";
import { PricedButton } from "../PricedButton";
import { Suggestions } from "../Suggestions";
import type { Brief as BriefT, Named, State } from "../types";

export const TYPEFACES = ["serif", "sans", "mono", "display"];
const STYLE_KEYS = ["medium", "palette", "lighting", "camera"] as const;
const BLANK: BriefT = {
  logline: "",
  style: { medium: "", palette: "", lighting: "", camera: "" },
  cast: [],
  places: [],
  avoid: [],
  look: { background: "#ffffff", colors: ["#000000"], typeface: "sans" },
};

function NamedList({ label, value, onChange }: { label: string; value: Named[]; onChange: (v: Named[]) => void }) {
  const set = (i: number, patch: Partial<Named>) => onChange(value.map((n, j) => (j === i ? { ...n, ...patch } : n)));
  return (
    <fieldset>
      <legend>{label}</legend>
      {value.map((n, i) => (
        <div className="row" key={i}>
          <input type="text" aria-label={`${label} name ${i + 1}`} value={n.name} onChange={(e) => set(i, { name: e.target.value })} />
          <input type="text" aria-label={`${label} description ${i + 1}`} value={n.description} onChange={(e) => set(i, { description: e.target.value })} />
          <button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...value, { name: "", description: "" }])}>Add</button>
    </fieldset>
  );
}

function BriefForm({ initial, onSave }: { initial: BriefT; onSave: (b: BriefT) => Promise<void> }) {
  const [b, setB] = useState(initial);
  const [error, setError] = useState("");
  const { look } = b;
  const setColor = (i: number, c: string) => setB({ ...b, look: { ...look, colors: look.colors.map((x, j) => (j === i ? c : x)) } });
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setError("");
        try {
          await onSave(b);
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }}
    >
      <label className="field"><span>Logline</span><input type="text" value={b.logline} onChange={(e) => setB({ ...b, logline: e.target.value })} /></label>
      {STYLE_KEYS.map((k) => (
        <label className="field" key={k}><span>{k}</span><input type="text" value={b.style[k]} onChange={(e) => setB({ ...b, style: { ...b.style, [k]: e.target.value } })} /></label>
      ))}
      <NamedList label="Cast" value={b.cast} onChange={(cast) => setB({ ...b, cast })} />
      <NamedList label="Places" value={b.places} onChange={(places) => setB({ ...b, places })} />
      <label className="field"><span>Avoid (one per line)</span><textarea value={b.avoid.join("\n")} onChange={(e) => setB({ ...b, avoid: e.target.value.split("\n").filter(Boolean) })} /></label>
      <fieldset>
        <legend>Look</legend>
        <label>Background <input type="color" value={look.background} onChange={(e) => setB({ ...b, look: { ...look, background: e.target.value } })} /></label>{" "}
        {look.colors.map((c, i) => <input key={i} type="color" aria-label={`Colour ${i + 1}`} value={c} onChange={(e) => setColor(i, e.target.value)} />)}
        <button type="button" disabled={look.colors.length >= 5} onClick={() => setB({ ...b, look: { ...look, colors: [...look.colors, "#000000"] } })}>Add colour</button>
        <button type="button" disabled={look.colors.length <= 1} onClick={() => setB({ ...b, look: { ...look, colors: look.colors.slice(0, -1) } })}>Remove colour</button>{" "}
        <label>Typeface <select value={look.typeface} onChange={(e) => setB({ ...b, look: { ...look, typeface: e.target.value } })}>{TYPEFACES.map((t) => <option key={t}>{t}</option>)}</select></label>
      </fieldset>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary">Save brief</button>
    </form>
  );
}

export function Brief({ state, refresh }: { state: State; refresh: () => void }) {
  const { project: p } = state;
  const pid = p.id;
  const [view, setView] = useState<View>(p.modes.brief === "auto" ? "auto" : loadView(pid, "brief"));
  const [seed, setSeed] = useState(p.seed);
  const [steer, setSteer] = useState("");
  const [draft, setDraft] = useState<BriefT | null>(null);
  const [error, setError] = useState("");
  const items = (state.candidates.brief as { items?: { id: string; brief: BriefT }[] } | null)?.items ?? [];

  const guard = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };
  const change = (v: View) =>
    guard(async () => {
      setView(v);
      saveView(pid, "brief", v);
      await putShotModes(pid, [], "brief", apiMode(v));
    });

  return (
    <section>
      <h2>Brief</h2>
      <ModeSwitch value={view} onChange={change} />
      {error && <p className="error">{error}</p>}
      {view === "auto" && (
        <>
          <label className="field"><span>Seed (a few words to start from, optional)</span><input type="text" value={seed} onChange={(e) => setSeed(e.target.value)} /></label>
          <PricedButton
            label="Let the model write it"
            price={state.prices.continue}
            onConfirm={async (price) => {
              await patchProject(pid, { seed });
              const r = await continueRun(pid, price);
              refresh();
              return r;
            }}
          />
        </>
      )}
      {view === "suggestions" && (
        <>
          <button className="primary" onClick={() => guard(() => suggestBrief(pid, steer))}>Suggest briefs</button>
          <Suggestions
            items={items}
            steer={steer}
            onSteer={setSteer}
            onUse={(id) => guard(() => useBrief(pid, id))}
            onEdit={(id) => {
              setDraft(items.find((i) => i.id === id)?.brief ?? null);
              setView("manual");
              saveView(pid, "brief", "manual");
            }}
            render={(it) => (
              <>
                <p><strong>{it.brief.logline}</strong></p>
                <p className="muted">{it.brief.style.medium} · {it.brief.style.palette}</p>
                <p>{it.brief.cast.map((c) => c.name).join(", ")} · {it.brief.places.map((c) => c.name).join(", ")}</p>
                <div className="swatches">
                  {[it.brief.look.background, ...it.brief.look.colors].map((c, i) => <i key={i} style={{ background: c }} title={c} />)}
                </div>
                <p className="muted">{it.brief.look.typeface}</p>
              </>
            )}
          />
        </>
      )}
      {view === "manual" && (
        <BriefForm key={JSON.stringify(draft)} initial={draft ?? state.brief ?? BLANK} onSave={async (b) => { await putBrief(pid, b); setDraft(null); refresh(); }} />
      )}
    </section>
  );
}
