import { useEffect, useState } from "react";
import { px, signed } from "./api";

/** Friendly bits for the Paper Exchange: the floating card stage, a battle-style HP bar for margin health, a mascot
 *  that reacts to P&L, Pokémon-style battle text after trades and a Poké Ball burst. Pure CSS + emoji, no sprites. */

export function CardStage({ image, name, mood = "flat", small = false }: { image?: string | null; name: string; mood?: "up" | "down" | "flat"; small?: boolean }) {
  return (
    <div className={`fx-stage is-${mood}${small ? " is-small" : ""}`} aria-hidden={!image}>
      <div className="fx-aura" />
      {image ? <img className="fx-card" src={image} alt={name} /> : <div className="fx-card fx-card-blank">?</div>}
      <div className="fx-shadow" />
    </div>
  );
}

/** Health of a leveraged position, battle style: full at entry, empty at the liquidation price. */
export function HpBar({ health, label = "HP" }: { health: number; label?: string }) {
  const h = Math.max(0, Math.min(1, health));
  const tone = h > 0.5 ? "good" : h > 0.2 ? "warn" : "bad";
  return (
    <div className={`fx-hp is-${tone}`} role="meter" aria-label="Position health" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(h * 100)}>
      <b>{label}</b>
      <span className="fx-hp-track"><i style={{ width: `${h * 100}%` }} /></span>
      <small>{Math.round(h * 100)}/100</small>
    </div>
  );
}

export function Mascot({ pnl, health = 1 }: { pnl: number; health?: number }) {
  const [face, line] = health < 0.2 ? ["😱", "About to faint!"] : pnl > 0 ? (pnl > 5_000 ? ["🤩", "Super effective!"] : ["😄", "Looking good!"])
    : pnl < 0 ? ["😰", "Hang in there…"] : ["🙂", "Ready!"];
  return <div className={`fx-mascot ${health < 0.2 ? "is-panic" : pnl > 0 ? "is-happy" : pnl < 0 ? "is-sad" : ""}`} title={line}><span>{face}</span><small>{line}</small></div>;
}

/** Pokémon-style dialogue box after a trade. `kind` drives the flavour; text stays factual. */
export type Battle = { text: string; flavour?: string; kind: "go" | "win" | "lose" | "info" | "bad"; at: number };

export function BattleBox({ b, onDone }: { b: Battle | null; onDone: () => void }) {
  useEffect(() => { if (!b) return; const t = window.setTimeout(onDone, 6000); return () => window.clearTimeout(t); }, [b, onDone]);
  if (!b) return null;
  return (
    <div key={b.at} className={`ex-msg fx-battle is-${b.kind}${b.kind === "bad" ? " is-bad" : ""}`} role="status" onClick={onDone}>
      {b.flavour && <b className="fx-flavour">{b.flavour}</b>}
      <span>{b.text}</span>
      <i className="fx-caret">▼</i>
    </div>
  );
}

export function PokeBurst({ at }: { at: number }) {
  const [on, setOn] = useState(false);
  useEffect(() => { if (!at) return; setOn(true); const t = window.setTimeout(() => setOn(false), 1100); return () => window.clearTimeout(t); }, [at]);
  if (!on) return null;
  return (
    <span className="fx-burst" aria-hidden>
      {Array.from({ length: 8 }, (_, i) => <i key={`${at}-${i}`} className="fx-ball" style={{ "--a": `${i * 45 + 10}deg` } as React.CSSProperties} />)}
    </span>
  );
}

/** "+12.34 USDT" with a little bounce whenever it changes sign. */
export function BigPnl({ cents, pct }: { cents: number; pct?: number | null }) {
  return (
    <div className={`fx-pnl ${cents > 0 ? "up" : cents < 0 ? "down" : ""}`}>
      <b>{signed(cents)}</b><small>USDT</small>
      {pct != null && <em>{pct >= 0 ? "+" : ""}{pct.toFixed(2)}%</em>}
    </div>
  );
}

export function flavourFor(pnl: number, pct: number | null): { flavour: string; kind: Battle["kind"] } {
  if (pct != null && pct >= 10) return { flavour: "A critical hit! 💥", kind: "win" };
  if (pnl > 0) return { flavour: "It's super effective! ✨", kind: "win" };
  if (pnl < 0) return { flavour: "It's not very effective… 💦", kind: "lose" };
  return { flavour: "It's a draw!", kind: "info" };
}

export const fmt = px;
