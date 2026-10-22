import { useCallback, useEffect, useState } from "react";
import { memberApi } from "../../../website/members";

interface Binder { cards: unknown[]; unique: number; pulls: number; in_portfolio?: boolean }

/**
 * Pack Giveaway pulls in the portfolio (opt-in). While on, the pulls are VIRTUAL holdings that follow the binder: they
 * count in this portfolio's value and chart, marked 🎁 Virtual, and never in a public value, the collectors' leaderboard
 * or the marketplace.
 */
export function GiveawayPulls({ onChanged }: { onChanged?: () => void }) {
  const [b, setB] = useState<Binder | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    void memberApi<Binder>("/members/packs/binder").then(setB).catch(() => setB(null));
  }, []);
  useEffect(load, [load]);
  async function show(on: boolean) {
    setBusy(true);
    try {
      await memberApi("/members/packs/portfolio", { method: "POST", body: JSON.stringify({ show: on }) });
      load();
      onChanged?.();
    } finally {
      setBusy(false);
    }
  }
  if (!b || b.pulls === 0) return null;
  return (
    <section className={`pf-box pf-gw-offer${b.in_portfolio ? " is-on" : ""}`}>
      <span aria-hidden>🎁</span>
      {b.in_portfolio ? (
        <p><b>Your {b.pulls} Pack Giveaway {b.pulls === 1 ? "card is" : "cards are"} in this portfolio</b> as virtual cards: counted in its
          value and chart, marked 🎁 Virtual, never on public leaderboards. They follow your binder as you open, sell or buy.</p>
      ) : (
        <p><b>Add your {b.pulls} Pack Giveaway {b.pulls === 1 ? "card" : "cards"} to this portfolio?</b> They'll count in its value and chart,
          marked 🎁 Virtual (not owned, not real), and never on public leaderboards.</p>
      )}
      <button type="button" className={b.in_portfolio ? "pf-link" : "pf-btn pf-shiny"} disabled={busy} onClick={() => void show(!b.in_portfolio)}>
        {b.in_portfolio ? "Remove them" : "Add to my portfolio"}
      </button>
    </section>
  );
}
