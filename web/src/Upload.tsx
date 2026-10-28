import { useState } from "react";

/** A file input plus a drop zone. `error` is the server's 422 text. */
export function Upload({ accept, onFile, error }: { accept: string; onFile: (f: File) => void; error?: string }) {
  const [over, setOver] = useState(false);
  return (
    <div
      className={`dropzone ${over ? "over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files[0];
        if (f) onFile(f);
      }}
    >
      <input type="file" accept={accept} aria-label="Choose a file" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      <span className="muted"> or drop a file here</span>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
