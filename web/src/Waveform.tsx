import { useEffect, useRef, useState } from "react";
import type { Window } from "./types";

type Range = [number | null, number | null];

/** One peak per pixel column: the largest absolute sample in that column's slice. */
export function peaksOf(data: Float32Array, n: number): number[] {
  const step = Math.max(1, Math.floor(data.length / n));
  return Array.from({ length: n }, (_, i) => {
    let m = 0;
    for (let j = i * step; j < Math.min(data.length, (i + 1) * step); j++) m = Math.max(m, Math.abs(data[j]));
    return m;
  });
}

interface Props {
  src: string;
  duration: number;
  windows: Window[];
  range: Range;
  onRange?: (r: [number, number]) => void;
}

export function Waveform({ src, duration, windows, range, onRange }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [peaks, setPeaks] = useState<number[] | null>(null);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const width = useRef(0);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const buf = await (await fetch(src)).arrayBuffer();
        const ctx = new AudioContext();
        const audio = await ctx.decodeAudioData(buf);
        void ctx.close();
        const w = Math.max(1, Math.floor(box.current?.clientWidth ?? 600));
        width.current = w;
        if (alive) setPeaks(peaksOf(audio.getChannelData(0), w));
      } catch (e) {
        if (alive) setError(`could not read the song: ${e instanceof Error ? e.message : e}`);
      }
    })();
    return () => {
      alive = false;
    };
  }, [src]);

  const sel: Range = drag ?? range;

  useEffect(() => {
    const c = canvas.current;
    if (!c || !peaks) return;
    const h = 120;
    c.width = peaks.length;
    c.height = h;
    const g = c.getContext("2d");
    if (!g) return;
    const css = getComputedStyle(document.documentElement);
    const x = (t: number) => (t / duration) * peaks.length;
    g.clearRect(0, 0, c.width, h);
    const a = x(sel[0] ?? 0), b = x(sel[1] ?? duration);
    g.fillStyle = "rgba(42,111,111,0.15)";
    g.fillRect(a, 0, b - a, h);
    peaks.forEach((p, i) => {
      g.fillStyle = i >= a && i <= b ? css.getPropertyValue("--accent") : css.getPropertyValue("--muted");
      g.fillRect(i, (h - p * h) / 2, 1, Math.max(1, p * h));
    });
    g.fillStyle = css.getPropertyValue("--ink");
    for (const w of windows) g.fillRect(Math.round(x(w.start)), 0, 1, h);
  }, [peaks, sel[0], sel[1], windows, duration]); // eslint-disable-line react-hooks/exhaustive-deps

  const at = (e: React.PointerEvent) => {
    const r = e.currentTarget.getBoundingClientRect();
    return Math.min(duration, Math.max(0, ((e.clientX - r.left) / r.width) * duration));
  };
  const origin = useRef(0);
  const down = (e: React.PointerEvent) => {
    if (!onRange) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    origin.current = at(e);
    setDrag([origin.current, origin.current]);
  };
  const move = (e: React.PointerEvent) => {
    if (drag) setDrag([Math.min(origin.current, at(e)), Math.max(origin.current, at(e))]);
  };
  const up = () => {
    if (drag && drag[1] > drag[0]) onRange?.(drag);
    setDrag(null);
  };

  if (error) return <p className="error">{error}</p>;
  return (
    <div className="wave" ref={box} onPointerDown={down} onPointerMove={move} onPointerUp={up} role="img" aria-label="song waveform with the selected range">
      <canvas ref={canvas} />
      {!peaks && <p className="muted" style={{ padding: ".5rem" }}>reading the song…</p>}
    </div>
  );
}
