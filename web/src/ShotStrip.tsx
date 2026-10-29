import type { ReactNode } from "react";
import type { Shot } from "./types";

/** One block per window, as wide as it is long, coloured by kind. */
export function ShotStrip({ shots, selected, onSelect, badge }: {
  shots: Shot[];
  selected: string | null;
  onSelect: (id: string) => void;
  badge: (shot: Shot) => ReactNode;
}) {
  return (
    <div className="shotstrip">
      {shots.map((s) => (
        <button key={s.id} className={`${s.kind} ${s.id === selected ? "here" : ""}`} style={{ flexGrow: s.end - s.start }} onClick={() => onSelect(s.id)}>
          {s.id} {badge(s)}
        </button>
      ))}
    </div>
  );
}
