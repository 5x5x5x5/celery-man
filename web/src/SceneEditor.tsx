import { useEffect, useRef, useState } from "react";
import { fileUrl } from "./api";
import { ScenePreview } from "./ScenePreview";
import type { Shot } from "./types";

const DEBOUNCE_MS = 400;

/** Scene code beside a live preview of the typed text; the parent saves it. */
export function SceneEditor({ pid, shot, text, onText }: { pid: string; shot: Shot; text: string; onText: (t: string) => void }) {
  const file = shot.scene?.file;
  const [shown, setShown] = useState(text);
  const latest = useRef(text);
  latest.current = text;
  useEffect(() => {
    if (text || !file) return;
    fetch(fileUrl(pid, file)).then((r) => (r.ok ? r.text() : "")).then((t) => latest.current || onText(t), () => {}); // never over typed text
  }, [pid, file]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const id = setTimeout(() => setShown(text), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [text]);
  return (
    <div>
      <textarea className="code" aria-label="Scene code" value={text} onChange={(e) => onText(e.target.value)} spellCheck={false} />
      {shown && <ScenePreview pid={pid} source={{ code: shown, params: shot.scene?.params ?? {} }} start={shot.start} end={shot.end} />}
    </div>
  );
}
