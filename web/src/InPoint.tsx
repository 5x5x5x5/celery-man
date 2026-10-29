import { useEffect, useRef } from "react";

const FPS = 24;

/** Snaps to a frame, then keeps the window inside the clip. */
export const clampIn = (v: number, clipDuration: number, window: number): number =>
  Math.min(Math.max(0, Math.round(v * FPS) / FPS), Math.max(0, clipDuration - window));

/** A bar as long as the clip with a handle as wide as the song window; drag to choose where the window starts. */
export function InPoint({ clipDuration, window: win, value, onChange }: {
  clipDuration: number;
  window: number;
  value: number;
  onChange: (v: number) => void;
}) {
  const bar = useRef<HTMLDivElement>(null);
  const move = useRef<((e: MouseEvent) => void) | null>(null);
  const at = (clientX: number) => {
    const r = bar.current!.getBoundingClientRect();
    onChange(clampIn(((clientX - r.left) / r.width) * clipDuration, clipDuration, win));
  };
  const stop = () => {
    if (move.current) document.removeEventListener("mousemove", move.current);
    move.current = null;
  };
  useEffect(() => stop, []);
  const down = (e: React.MouseEvent) => {
    stop();
    at(e.clientX);
    move.current = (m) => at(m.clientX);
    document.addEventListener("mousemove", move.current);
    document.addEventListener("mouseup", stop, { once: true });
  };
  const max = Math.max(0, clipDuration - win);
  return (
    <div
      ref={bar}
      className="inpoint"
      role="slider"
      tabIndex={0}
      aria-label="In point"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      onMouseDown={down}
      onKeyDown={(e) => {
        const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (d) onChange(clampIn(value + d / FPS, clipDuration, win));
      }}
    >
      <i style={{ left: `${(value / clipDuration) * 100}%`, width: `${(Math.min(win, clipDuration) / clipDuration) * 100}%` }} />
    </div>
  );
}
