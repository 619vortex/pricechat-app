/** Risk-limit changes that let a trade lose more money, in words (empty when nothing got riskier). Used before saving limits on a
 *  LIVE account, in the Earn terminal's settings and the Kiwi Brain control room. */
export interface RiskLimits { max_leverage: number; max_loss_per_trade_usd: number; daily_loss_limit_usd: number }

const usd = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function riskier(old: RiskLimits, next: RiskLimits): string[] {
  const out: string[] = [];
  if (next.max_leverage > old.max_leverage) out.push(`Max leverage ${old.max_leverage}x → ${next.max_leverage}x`);
  if (next.max_loss_per_trade_usd > old.max_loss_per_trade_usd) out.push(`Max loss per trade ${usd(old.max_loss_per_trade_usd)} → ${usd(next.max_loss_per_trade_usd)}`);
  if (next.daily_loss_limit_usd > old.daily_loss_limit_usd) out.push(`Daily loss limit ${usd(old.daily_loss_limit_usd)} → ${usd(next.daily_loss_limit_usd)}`);
  return out;
}

/** Asks once when a LIVE account's new limits allow more real-money risk; true when it's fine to save. */
export function okToSaveLimits(mode: string, old: RiskLimits, next: RiskLimits): boolean {
  const more = mode === "LIVE" ? riskier(old, next) : [];
  return !more.length || window.confirm(`LIVE account: this allows more risk with real money.\n\n${more.join("\n")}\n\nSave these limits?`);
}
