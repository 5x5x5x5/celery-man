import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { fileUrl } from "./api";
import { isPlaying, pause, playWindow, seek, setSong, useSongTime } from "./clock";
import { itemAt, withOverride } from "./roughcut";
import { ScenePreview } from "./ScenePreview";
import { useTryInPlace } from "./TryInPlace";
import type { CutItem, State } from "./types";

const DRIFT = 0.08;

function Clip({ src, at }: { src: string; at: number }) {
  const v = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const el = v.current;
    if (el && el.readyState > 0 && Math.abs(el.currentTime - at) > DRIFT) el.currentTime = at;
  }, [at]);
  return <video ref={v} src={src} muted preload="auto" onLoadedMetadata={(e) => (e.currentTarget.currentTime = at)} />;
}

/** Shows what the cut has at song time t. */
export function Stage({ state, cut, t }: { state: State; cut: CutItem[]; t: number }) {
  const { override } = useTryInPlace();
  const pid = state.project.id;
  const look = state.brief?.look;
  const bg = look?.background ?? "#000";
  const hit = itemAt(cut, t);
  let body: ReactNode = null;
  if (hit) {
    const { item, local } = hit;
    const file = item.src ? fileUrl(pid, item.src) : "";
    if (override?.candidate && override.shot === item.shot) {
      body = <ScenePreview pid={pid} sid={item.shot} candidate={override.candidate} start={item.start} end={item.end} />;
    } else if ((item.kind === "take" || item.kind === "render") && file) {
      body = <Clip key={file} src={file} at={item.in + local} />;
    } else if (item.kind === "still" && file) {
      body = <img src={file} alt="" style={{ transform: `scale(${1 + (0.06 * local) / (item.end - item.start)})` }} />;
    } else if (item.kind === "card") {
      body = <p style={{ color: look?.colors[0], fontFamily: look?.typeface }}>{item.text}</p>;
    }
  }
  return <div className="stage" style={{ background: bg }}>{body}</div>;
}

/** Stage, transport and a strip of every window; `decided` adds a marker per shot. */
export function Player({ state, decided }: { state: State; decided?: (shot: string) => ReactNode }) {
  const { override } = useTryInPlace();
  const pid = state.project.id;
  const cut = withOverride(state.cut, override);
  const t = useSongTime();
  useEffect(() => setSong(fileUrl(pid, state.project.song)), [pid, state.project.song]);
  const end = cut.length ? cut[cut.length - 1].end : 0;
  const here = itemAt(cut, t)?.item;
  return (
    <div className="player">
      <Stage state={state} cut={cut} t={t} />
      <div className="row">
        <button onClick={() => (isPlaying() ? pause() : playWindow(t >= end ? 0 : t, end, false))} disabled={!cut.length}>
          {isPlaying() ? "Pause" : "Play"}
        </button>
        <span className="muted">{t.toFixed(1)}s</span>
      </div>
      <div className="cutstrip">
        {cut.map((c) => (
          <button
            key={c.shot + c.start}
            className={`${c.kind}${c === here ? " here" : ""}`}
            style={{ flexGrow: c.end - c.start }}
            title={`${c.shot}: ${c.kind}`}
            onClick={() => seek(c.start)}
          >
            {c.shot}
            {decided?.(c.shot)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The collapsible dock at the bottom of every step screen. */
export function RoughCut({ state }: { state: State }) {
  const [open, setOpen] = useState(true);
  return (
    <aside className="dock">
      <button onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide rough cut" : "Show rough cut"}</button>
      {open && (state.cut.length ? <Player state={state} /> : <p className="muted">No cut yet.</p>)}
    </aside>
  );
}
