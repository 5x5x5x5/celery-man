import type { ReactNode } from "react";
import type { Override } from "./roughcut";
import { tryProps, useTryInPlace } from "./TryInPlace";

interface Props<T extends { id: string }> {
  items: T[];
  render: (item: T) => ReactNode;
  onUse: (id: string) => void;
  onEdit?: (id: string) => void;
  /** What this suggestion would put in the rough cut; hover / focus tries it there. */
  tryout?: (item: T) => Override | null;
  steer: string;
  onSteer: (text: string) => void;
}

export function Suggestions<T extends { id: string }>({ items, render, onUse, onEdit, tryout, steer, onSteer }: Props<T>) {
  const { setOverride } = useTryInPlace();
  return (
    <div>
      <div className="cards">
        {items.map((it) => (
          <article className="card" key={it.id} {...tryProps(setOverride, tryout?.(it) ?? null)}>
            {render(it)}
            <div className="row">
              <button className="primary" onClick={() => onUse(it.id)}>Use this</button>
              {onEdit && <button onClick={() => onEdit(it.id)}>Edit</button>}
            </div>
          </article>
        ))}
      </div>
      <label className="field">
        <span>Steer the next suggestions</span>
        <input type="text" value={steer} onChange={(e) => onSteer(e.target.value)} />
      </label>
    </div>
  );
}
