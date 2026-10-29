import { useState } from "react";
import { createProject, fileUrl, patchProject } from "../api";
import { KINDS } from "../types";
import type { Kind, State } from "../types";
import { Waveform } from "../Waveform";

function Kinds({ value, onChange }: { value: Kind[]; onChange: (k: Kind[]) => void }) {
  return (
    <fieldset>
      <legend>Kinds of shot</legend>
      {KINDS.map((k) => (
        <label key={k} style={{ marginRight: "1rem" }}>
          <input
            type="checkbox"
            checked={value.includes(k)}
            onChange={(e) => onChange(e.target.checked ? KINDS.filter((x) => x === k || value.includes(x)) : value.filter((x) => x !== k))}
          />{" "}
          {k}
        </label>
      ))}
    </fieldset>
  );
}

export function NewProject({ onCreated }: { onCreated: (pid: string) => void }) {
  const [name, setName] = useState("");
  const [song, setSong] = useState<File | null>(null);
  const [source, setSource] = useState("");
  const [kinds, setKinds] = useState<Kind[]>(["footage"]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!song) return;
    setBusy(true);
    setError("");
    try {
      onCreated((await createProject({ name, song, source, kinds })).id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <label className="field"><span>Name (letters, digits, - or _)</span><input type="text" value={name} onChange={(e) => setName(e.target.value)} required /></label>
      <label className="field"><span>Song file</span><input type="file" accept="audio/*" onChange={(e) => setSong(e.target.files?.[0] ?? null)} required /></label>
      <label className="field"><span>Source (where the song comes from, optional)</span><input type="text" value={source} onChange={(e) => setSource(e.target.value)} /></label>
      <Kinds value={kinds} onChange={setKinds} />
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy || !song || !kinds.length}>Create</button>
    </form>
  );
}

export function Song({ state, refresh }: { state: State; refresh: () => void }) {
  const { project: p, timing } = state;
  const [error, setError] = useState("");

  const patch = async (body: Parameters<typeof patchProject>[1]) => {
    setError("");
    try {
      let r = await patchProject(p.id, body);
      if (!r.ok && window.confirm("Decided shots will reopen and their choices will be cleared. Change anyway?")) {
        r = await patchProject(p.id, { ...body, reset_shots: true });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  return (
    <section>
      <h2>Song</h2>
      <p className="muted">{p.song}{p.song_source && ` · ${p.song_source}`}</p>
      {timing ? (
        <>
          <Waveform src={fileUrl(p.id, p.song)} duration={timing.duration} windows={timing.windows} range={p.range} onRange={(r) => patch({ range: r })} />
          <p className="muted">
            Drag on the waveform to pick the part to make. {timing.windows.length} windows
            {p.range[0] !== null && ` · ${p.range[0].toFixed(1)}s to ${p.range[1]?.toFixed(1)}s`}
            {" "}
            <button onClick={() => patch({ range: [null, null] })}>Use the whole song</button>
          </p>
        </>
      ) : (
        <p className="muted">Finding the beat…</p>
      )}
      <Kinds value={p.kinds} onChange={(k) => k.length && patch({ kinds: k })} />
      {error && <p className="error">{error}</p>}
    </section>
  );
}
