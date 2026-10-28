import { useEffect, useState } from "react";
import { fileUrl } from "./api";
import { ScenePreview } from "./ScenePreview";
import type { Shot } from "./types";

/** Scene code beside a live preview of the saved scene. shortcut: the API has no route to save code, so edits are
 * read-and-try text only; add a PUT for scene files when saving code is wanted. */
export function SceneEditor({ pid, shot, code }: { pid: string; shot: Shot; code?: string }) {
  const file = shot.scene?.file;
  const [text, setText] = useState(code ?? "");
  useEffect(() => {
    if (code !== undefined || !file) return;
    fetch(fileUrl(pid, file)).then((r) => (r.ok ? r.text() : "")).then(setText, () => {});
  }, [pid, file, code]);
  return (
    <div>
      <textarea className="code" aria-label="Scene code" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      <ScenePreview pid={pid} sid={shot.id} start={shot.start} end={shot.end} />
    </div>
  );
}
