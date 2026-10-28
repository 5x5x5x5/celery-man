import type { ReactNode } from "react";

interface Props<T extends { id: string }> {
  items: T[];
  render: (item: T) => ReactNode;
  onUse: (id: string) => void;
  onEdit?: (id: string) => void;
  steer: string;
  onSteer: (text: string) => void;
}

export function Suggestions<T extends { id: string }>({ items, render, onUse, onEdit, steer, onSteer }: Props<T>) {
  return (
    <div>
      <div className="cards">
        {items.map((it) => (
          <article className="card" key={it.id}>
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
