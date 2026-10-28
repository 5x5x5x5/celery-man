import { useEffect, useState } from "react";
import { SceneEditor } from "./SceneEditor";
import { KINDS } from "./types";
import type { Brief, Kind, ParamSpec, Shot, Templates } from "./types";

const FIELDS = ["kind", "action", "cast", "place", "framing", "scene"];
/** The field a server message names: it starts with the field's name. */
const fieldOf = (msg: string) => FIELDS.find((f) => new RegExp(`^${f}\\b`).test(msg));

const defaults = (specs: Record<string, ParamSpec>) => Object.fromEntries(Object.entries(specs).map(([k, s]) => [k, s.default]));

/** An animation shot always has a scene: the first template with its default params. */
function withScene(d: Shot, templates: Templates): Shot {
  const first = Object.keys(templates)[0];
  return d.kind === "animation" && !d.scene && first ? { ...d, scene: { template: first, params: defaults(templates[first].params) } } : d;
}

function Param({ name, spec, value, onChange }: { name: string; spec: ParamSpec; value: unknown; onChange: (v: unknown) => void }) {
  const label = `${name}${spec.doc ? ` (${spec.doc})` : ""}`;
  let input;
  if (spec.type === "choice")
    input = <select value={String(value)} onChange={(e) => onChange(e.target.value)}>{spec.choices?.map((c) => <option key={c}>{c}</option>)}</select>;
  else if (spec.type === "bool") input = <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  else if (spec.type === "string") input = <input type="text" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  else
    input = (
      <input type="number" min={spec.min} max={spec.max} step={spec.type === "integer" ? 1 : "any"} value={Number(value)} onChange={(e) => onChange(e.target.valueAsNumber)} />
    );
  return <label className="field"><span>{label}</span>{input}</label>;
}

export function ShotForm({ pid, shot, brief, kinds, templates, code, onSave }: {
  pid: string;
  shot: Shot;
  brief: Brief;
  kinds: Kind[];
  templates: Templates;
  code?: string;
  onSave: (s: Shot, code?: string) => Promise<void>;
}) {
  const open = shot.kind === "open";
  const [d, setD] = useState<Shot>(() => withScene({ ...shot, kind: open ? kinds[0] : shot.kind }, templates));
  const [codeText, setCodeText] = useState(code ?? "");
  // the catalogue may arrive after the form
  useEffect(() => setD((x) => withScene(x, templates)), [templates]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const names = Object.keys(templates);
  const scene = d.scene;
  const usesCode = !!scene && !scene.template;

  const setKind = (k: Kind) => setD(withScene({ ...d, kind: k, scene: k === "footage" ? null : d.scene }, templates));
  const setTemplate = (t: string) => setD({ ...d, scene: { template: t, params: defaults(templates[t].params) } });

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await onSave(d, usesCode && codeText ? codeText : undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const bad = fieldOf(error);
  const err = (f: string) => (bad === f ? <p className="err-inline" role="alert">{error}</p> : null);

  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <fieldset>
        <legend>Kind</legend>
        {KINDS.filter((k) => kinds.includes(k)).map((k) => (
          <label key={k} style={{ marginRight: "1rem" }}>
            <input type="radio" name={`kind-${d.id}`} checked={d.kind === k} onChange={() => setKind(k)} /> {k}
          </label>
        ))}
        {err("kind")}
      </fieldset>
      {d.kind === "footage" && (
        <>
          <label className="field"><span>Action</span><input type="text" value={d.action} onChange={(e) => setD({ ...d, action: e.target.value })} />{err("action")}</label>
          <fieldset>
            <legend>Cast</legend>
            {brief.cast.map((c) => (
              <label key={c.name} style={{ marginRight: "1rem" }}>
                <input type="checkbox" checked={d.cast.includes(c.name)} onChange={(e) => setD({ ...d, cast: e.target.checked ? [...d.cast, c.name] : d.cast.filter((x) => x !== c.name) })} /> {c.name}
              </label>
            ))}
            {err("cast")}
          </fieldset>
          <label className="field">
            <span>Place</span>
            <select value={d.place ?? ""} onChange={(e) => setD({ ...d, place: e.target.value || null })}>
              <option value="">none</option>
              {brief.places.map((p) => <option key={p.name}>{p.name}</option>)}
            </select>
            {err("place")}
          </label>
          <label className="field"><span>Framing</span><input type="text" value={d.framing} onChange={(e) => setD({ ...d, framing: e.target.value })} />{err("framing")}</label>
        </>
      )}
      {d.kind === "animation" && scene && (
        <>
          <label className="field">
            <span>Scene</span>
            <select aria-label="Scene template" value={usesCode ? "" : scene.template} onChange={(e) => (e.target.value ? setTemplate(e.target.value) : setD({ ...d, scene: { file: scene.file ?? "scenes/scene.js", params: {} } }))}>
              {names.map((n) => <option key={n}>{n}</option>)}
              <option value="">my own code</option>
            </select>
          </label>
          {!usesCode && scene.template && templates[scene.template] &&
            Object.entries(templates[scene.template].params).map(([k, spec]) => (
              <Param key={k} name={k} spec={spec} value={scene.params[k] ?? spec.default} onChange={(v) => setD({ ...d, scene: { ...scene, params: { ...scene.params, [k]: v } } })} />
            ))}
          {usesCode && (
            <>
              <label className="field"><span>Scene file</span><input type="text" value={scene.file ?? ""} onChange={(e) => setD({ ...d, scene: { ...scene, file: e.target.value } })} /></label>
              <SceneEditor pid={pid} shot={d} text={codeText} onText={setCodeText} />
            </>
          )}
          {err("scene")}
        </>
      )}
      {error && !bad && <p className="error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}>Save</button>
    </form>
  );
}
