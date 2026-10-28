import type { Spend } from "./types";

export function SpendMeter({ spend }: { spend: Spend }) {
  const usd = (n: number) => `$${n.toFixed(2)}`;
  return (
    <span className="spend" title={`${usd(spend.reserve)} is held back for the quick fallback`}>
      spent {usd(spend.spent)} of {usd(spend.cap)} cap
    </span>
  );
}
