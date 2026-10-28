import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { Override } from "./roughcut";

interface Ctx {
  override: Override | null;
  setOverride: (o: Override | null) => void;
}
const TryInPlace = createContext<Ctx>({ override: null, setOverride: () => {} });

export function TryInPlaceProvider({ children }: { children: ReactNode }) {
  const [override, setOverride] = useState<Override | null>(null);
  const value = useMemo(() => ({ override, setOverride }), [override]);
  return <TryInPlace.Provider value={value}>{children}</TryInPlace.Provider>;
}

export const useTryInPlace = (): Ctx => useContext(TryInPlace);

/** Hover / focus props for a suggestion card: tries `o` in the rough cut, clears on leave / blur. */
export const tryProps = (set: Ctx["setOverride"], o: Override | null) => ({
  onMouseEnter: () => o && set(o),
  onFocus: () => o && set(o),
  onMouseLeave: () => set(null),
  onBlur: () => set(null),
});
