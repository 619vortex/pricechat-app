import { site } from "../websiteData";
import type { Ev } from "./api";

/* The Kiwi reacts to the latest REAL event (within 30 min). Position moods only come from position events (the backend
   confirmed a fill or a close); a strategy proposal in shadow mode says it wasn't traded. */
export type Mood = { key: string; prop: string; line: string };

export function moodOf(evs: Ev[]): Mood {
  const recent = evs.filter((e) => Date.now() - new Date(e.at).getTime() < 30 * 60_000 && e.kind !== "market");
  const e = recent[0];
  if (!e) return { key: "idle", prop: "☕", line: "No signal. Kiwi drinks coffee and waits." };
  const d = e.data as Record<string, unknown>;
  if (e.kind === "position" && /opened/.test(e.title)) return { key: "glasses", prop: "🕶️", line: "Position open. Glasses on." };
  if (e.kind === "position" && /closed/.test(e.title)) {
    const net = Number(d.net_pnl ?? 0);
    return net > 0 ? { key: "party", prop: "🎉", line: "Closed in profit. KIWI EATS TONIGHT." }
      : { key: "sad", prop: "💧", line: /stop/.test(String(d.reason ?? e.title)) ? "Stop-loss hit. Pain." : "Closed at a loss. The bird lives another day 💀" };
  }
  if (e.kind === "signal" && d.after === "TARGET_HIT") return { key: "party", prop: "🎉", line: "Signal target hit." };
  if (e.kind === "signal" && d.after === "STOP_HIT") return { key: "sad", prop: "💧", line: "Signal stopped out." };
  if (e.kind === "risk" && (d.decision === "rejected" || d.decision === "skipped")) return { key: "nope", prop: "🙅", line: "NAH BRO, THAT'S EXIT LIQUIDITY 💀" };
  if (e.kind === "strategy" && (d.decision === "LONG" || d.decision === "SHORT"))
    return { key: "perk", prop: "❗", line: d.shadow ? `Spotted a ${String(d.decision).toLowerCase()} on ${e.coin} (shadow: not traded).` : `${e.coin} ${String(d.decision)} setup!` };
  if (e.kind === "strategy") return { key: "nope", prop: "🙅", line: `NAH BRO, ${e.coin} IS EXIT LIQUIDITY 💀` };
  return { key: "idle", prop: "☕", line: "Watching the markets." };
}

export function KiwiMood({ mood, size = 56 }: { mood: Mood; size?: number }) {
  return (
    <span className={`kbm is-${mood.key}`} style={{ width: size, height: size }} title={mood.line}>
      <img src={site.kiwi.src} alt="" width={size} height={Math.round(size * 0.844)} />
      <span className="kbm-prop" aria-hidden>{mood.prop}</span>
    </span>
  );
}
