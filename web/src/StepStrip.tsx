import { STEPS } from "./types";
import type { Step, StepState } from "./types";

// state.steps names the pipeline's stages; the strip's "song" is its "timing", "cut" is "assemble".
const STAGE: Record<Step, string> = { song: "timing", brief: "brief", shots: "shots", keyframes: "keyframes", takes: "takes", cut: "assemble" };

export function StepStrip({ pid, here, steps }: { pid: string; here: Step; steps: StepState[] }) {
  return (
    <nav className="steps">
      {STEPS.map((s) => {
        const st = steps.find((x) => x.step === STAGE[s]);
        return (
          <a key={s} href={`#/p/${pid}/${s}`} className={`${st?.state ?? ""} ${s === here ? "here" : ""}`} title={st?.detail || st?.state}>
            <span className="dot" />
            {s}
          </a>
        );
      })}
    </nav>
  );
}
