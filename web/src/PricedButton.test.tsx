import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { PricedButton } from "./PricedButton";

test("priced button re-asks at the new price", async () => {
  const onConfirm = vi.fn().mockResolvedValueOnce({ ok: false, price: 0.2 }).mockResolvedValueOnce({ ok: true });
  render(<PricedButton label="Go" price={0.1} onConfirm={onConfirm} />);
  fireEvent.click(screen.getByRole("button", { name: "Go · $0.10" }));
  await screen.findByRole("button", { name: "Go · $0.20" });
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(onConfirm).toHaveBeenLastCalledWith(0.2));
});

test("a reason disables the button and is shown", async () => {
  render(<PricedButton label="Go" price={0.1} onConfirm={async () => ({ ok: false, reason: "reserve" })} />);
  fireEvent.click(screen.getByRole("button"));
  await screen.findByText("reserve");
  expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true);
});
afterEach(cleanup);
