import { useEffect, useState } from "react";
import { continueRun } from "./api";
import type { Paid } from "./api";
import type { State } from "./types";

const usd = (n: number) => `$${n.toFixed(2)}`;

/** Shows its price. If the server's price differs, the label updates and the next click confirms the new price. */
export function PricedButton({ label, price, onConfirm, disabledReason }: {
  label: string;
  price: number;
  onConfirm: (price: number) => Promise<Paid>;
  disabledReason?: string;
}) {
  const [shown, setShown] = useState(price);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => setShown(price), [price]);
  useEffect(() => setReason(""), [price, disabledReason]);

  const click = async () => {
    setBusy(true);
    setReason("");
    try {
      const r = await onConfirm(shown);
      if (!r.ok) {
        if (r.reason) setReason(r.reason);
        else if (r.price !== undefined) setShown(r.price);
      }
    } catch (e) {
      setReason(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const why = disabledReason ?? reason;
  return (
    <span>
      <button className="primary" disabled={busy || !!why} onClick={click}>{`${label} · ${usd(shown)}`}</button>
      {why && <span className="muted"> {why}</span>}
    </span>
  );
}

export function ContinueButton({ pid, state, onDone }: { pid: string; state: State; onDone?: () => void }) {
  const price = state.prices.continue;
  const idle = price === 0 && !state.steps.some((s) => s.state === "missing" || s.state === "stale");
  return (
    <PricedButton
      label="Continue"
      price={price}
      disabledReason={idle ? "nothing to do" : undefined}
      onConfirm={async (p) => {
        const r = await continueRun(pid, p);
        if (r.ok) onDone?.();
        return r;
      }}
    />
  );
}
