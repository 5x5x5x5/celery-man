import type { Mode } from "./types";

export type View = "auto" | "suggestions" | "manual";
const LABELS: [View, string][] = [["auto", "Auto"], ["suggestions", "Suggestions"], ["manual", "Manual"]];

/** Suggestions and manual are both the API's tastemaker mode; only the view differs. */
export const apiMode = (v: View): Mode => (v === "auto" ? "auto" : "tastemaker");

const key = (shot: string) => `view:${shot}`;
/** The UI-only suggestions/manual choice for a shot. */
export function loadView(shot: string): "suggestions" | "manual" {
  try {
    return localStorage.getItem(key(shot)) === "manual" ? "manual" : "suggestions";
  } catch {
    return "suggestions";
  }
}
export function saveView(shot: string, v: View): void {
  try {
    if (v !== "auto") localStorage.setItem(key(shot), v);
  } catch {
    /* storage unavailable: the choice just won't persist */
  }
}

export function ModeSwitch({ value, onChange }: { value: View; onChange: (v: View) => void }) {
  return (
    <div className="modeswitch" role="group" aria-label="Mode">
      {LABELS.map(([v, label]) => (
        <button key={v} aria-pressed={v === value} className={v === value ? "primary" : ""} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  );
}
