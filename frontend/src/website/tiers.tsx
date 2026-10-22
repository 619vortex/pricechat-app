/** Hyperliquid deposit tiers ($10 / $100 / $1K / $10K): reached ones lit, the next one glowing, with the XP each pays. */
export interface Tiers { deposited: number; tiers: { usd: number; xp: number; name: string; reached: boolean }[]; current: string | null;
  next: { usd: number; xp: number; name: string; missing: number } | null }

const short = (u: number) => (u >= 1000 ? `$${u / 1000}K` : `$${u}`);

export function TierLadder({ t }: { t: Tiers }) {
  return (
    <ul className="tier-ladder" aria-label="Deposit tiers">
      {t.tiers.map((x) => (
        <li key={x.usd} className={x.reached ? "is-reached" : t.next?.usd === x.usd ? "is-next" : ""}>
          <b>{short(x.usd)}</b><span>{x.name}</span><span>{x.reached ? "✓" : `+${x.xp} XP`}</span>
        </li>
      ))}
    </ul>
  );
}
