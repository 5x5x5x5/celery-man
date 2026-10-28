import { useEffect, useRef, useState } from "react";
import { getFrames, previewUrl } from "./api";
import { frameIndex, useSongTime } from "./clock";

const SIZE = { width: 960, height: 544, fps: 24 };

export function ScenePreview({ pid, sid, candidate, start, end }: { pid: string; sid?: string; candidate?: string; start: number; end: number }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [doc, setDoc] = useState<string>();
  const [error, setError] = useState("");
  const [frames, setFrames] = useState<Record<string, unknown>[]>([]);
  const [ready, setReady] = useState(false); // the scene has finished its async setup
  const t = useSongTime();

  useEffect(() => {
    if (!sid) return;
    let alive = true;
    setError("");
    setDoc(undefined);
    fetch(previewUrl(pid, sid, candidate))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`preview ${r.status}`))))
      .then((h) => alive && setDoc(h), (e) => alive && setError(String(e.message ?? e)));
    getFrames(pid, start, end).then((f) => alive && setFrames(f as Record<string, unknown>[]), (e) => alive && setError(String(e.message ?? e)));
    return () => {
      alive = false;
    };
  }, [pid, sid, candidate, start, end]);

  useEffect(() => setReady(false), [doc]);

  // only the iframe's own window may speak; its text is shown as text
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return;
      if (e.data?.type === "ready") setReady(true);
      else if (e.data?.type === "error") setError(String(e.data.message));
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, []);

  const init = () => {
    frame.current?.contentWindow?.postMessage({ type: "init", payload: { ...SIZE, duration: end - start } }, "*");
  };

  useEffect(() => {
    if (!ready || t < start || t >= end) return;
    const i = Math.min(frameIndex(t, start, SIZE.fps), frames.length - 1);
    if (i >= 0) frame.current?.contentWindow?.postMessage({ type: "frame", i, f: frames[i] }, "*");
  }, [ready, t, start, end, frames]);

  return (
    <div className="preview">
      <iframe ref={frame} sandbox="allow-scripts" srcDoc={doc} onLoad={doc ? init : undefined} title="Scene preview" />
      {error && <p className="error">{error}</p>}
    </div>
  );
}
