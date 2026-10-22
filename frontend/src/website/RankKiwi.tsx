import { site } from "./websiteData";
import type { RankArt } from "./members";
import { RANK_ASSETS } from "./rankAssets";
import { AuraGlow, decoKey, HoloSheen, RankGear, RankStage } from "./rankDeco";

/**
 * A member's Kiwi at their rank.
 *  - Official rank artwork (rankAssets.ts, or art.asset from the server) is used whenever it exists.
 *  - Until then, the development stand-in: ALWAYS the canonical Kiwi (site.kiwi) with a fixed CSS treatment per rank
 *    (scale, gold finish), dressed by rankDeco.tsx: a backdrop, gear and animated effects drawn around it.
 * The canonical Kiwi itself is never redrawn or altered in shape.
 */
export const WORKER_OVERLAY = "/brand/worker-overlay.svg";

/** worker: the construction-worker Kiwi (hard hat + hi-vis vest drawn over the canonical Kiwi; the rank styling stays). */
export function RankKiwi({ art, size = 120, label, rankKey, worker = false }: { art: RankArt & { asset?: string | null }; size?: number; label?: string;
  rankKey?: string; worker?: boolean }) {
  const { src, width, height } = site.kiwi;
  const official = worker ? null : art.asset ?? (rankKey ? RANK_ASSETS[rankKey] : null) ?? null;
  const overlay = worker ? WORKER_OVERLAY : art.overlay;
  const a11y = { role: label ? "img" : undefined, "aria-label": label, "aria-hidden": label ? undefined : true } as const;
  if (official) {
    return (
      <span className="rk rk-official" style={{ width: size, height: size }} {...a11y}>
        <img className="rk-asset" src={official} width={size} height={size} alt="" decoding="async" loading="lazy" />
      </span>
    );
  }
  const k = decoKey(rankKey, art.treatment);
  const small = size < 60;
  return (
    <span className={`rk rk-${art.treatment} rkd-${k}${small ? " is-small" : ""}`} style={{ width: size, height: size }} {...a11y}>
      <RankStage k={k} layer="back" small={small} />
      <span className="rk-fig">
        {(k === "legend" || k === "mythic") && !small ? <AuraGlow src={src} k={k} /> : null}
        <RankGear k={k} layer="back" />
        <img className="rk-kiwi" src={src} width={width} height={height} alt="" decoding="async" loading="lazy" />
        {overlay ? <img className="rk-overlay" src={overlay} width={width} height={height} alt="" decoding="async" loading="lazy" /> : null}
        {k === "mythic" && !small ? <HoloSheen src={src} /> : null}
        <RankGear k={k} layer="front" />
      </span>
      <RankStage k={k} layer="front" small={small} />
    </span>
  );
}
