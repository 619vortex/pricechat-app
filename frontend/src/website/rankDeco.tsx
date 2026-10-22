import { useId, type CSSProperties, type ReactNode } from "react";
import "./rankDeco.css";

/**
 * Rank decoration around the canonical Kiwi (the Kiwi itself is never redrawn: everything here sits behind it, in front of
 * it, or around it). Two layers:
 *  - RankStage: the whole square (viewBox 100 x 100): a coloured backdrop per rank, rays, flames, rising sparks, the egg
 *    shell and the chick's shell cup.
 *  - RankGear: drawn in the Kiwi image's own pixels (viewBox 1000 x 844), so gear lands on the same spot at every size:
 *    backpack, sword and headband, shield, cape and crown, laurel, halo.
 * Official rank artwork (rankAssets.ts) replaces all of this. Small sizes (header, lists) keep the backdrop and gear only.
 * Motion is CSS (rankDeco.css), transform / opacity only, off for reduced motion.
 */

type Key = "egg" | "chick" | "kiwi" | "scout" | "warrior" | "knight" | "royal" | "golden" | "legend" | "mythic";

const BY_TREATMENT: Record<string, Key> = { egg: "egg", chick: "chick", none: "kiwi", gold: "golden", aura: "legend", mythic: "mythic" };
const KNOWN = new Set<Key>(["egg", "chick", "kiwi", "scout", "warrior", "knight", "royal", "golden", "legend", "mythic"]);

export function decoKey(rankKey: string | undefined, treatment: string): Key {
  return rankKey && KNOWN.has(rankKey as Key) ? (rankKey as Key) : BY_TREATMENT[treatment] ?? "kiwi";
}

/** Backdrop colour [core, edge] per rank. */
const TINT: Record<Key, [string, string]> = {
  egg: ["#7fe8c2", "#1d5c48"], chick: ["#ffe08a", "#8a6a1d"], kiwi: ["#00e58f", "#0a5c3c"], scout: ["#8fd16a", "#2f5e22"],
  warrior: ["#ff7a45", "#7a1d10"], knight: ["#7fb6ff", "#1d3f7a"], royal: ["#b78cff", "#43207a"], golden: ["#ffd24d", "#8a5a00"],
  legend: ["#29ffb0", "#00593a"], mythic: ["#ffffff", "#3a2a7a"],
};

/** Rising sparks: x, start y, size, delay (s), duration (s). Fixed, so every Kiwi of a rank looks the same. */
const SPARKS: [number, number, number, number, number][] = [
  [22, 78, 1.6, 0, 3.4], [78, 74, 1.3, 1.1, 3.9], [34, 88, 1.1, 2.0, 3.1], [66, 86, 1.8, 0.6, 4.2], [50, 80, 1.2, 2.7, 3.6],
  [14, 60, 1.0, 1.7, 4.4], [86, 58, 1.4, 3.1, 3.8], [42, 70, 0.9, 3.6, 3.3],
];
const SPARK_COLOR: Partial<Record<Key, string[]>> = {
  kiwi: ["#7dffc8"], scout: ["#c8ff8a", "#7dffc8"], warrior: ["#ffb347", "#ff6a2b", "#ffd27a"], knight: ["#cfe3ff", "#7fb6ff"],
  royal: ["#ffd24d", "#e3c7ff"], golden: ["#fff1a8", "#ffd24d"], legend: ["#7dffc8", "#29ffb0", "#eafff6"],
  mythic: ["#ffffff", "#ffd24d", "#9ad8ff", "#ff9ae6", "#7dffc8"],
};
const STAR = "M0 -1 L0.28 -0.28 L1 0 L0.28 0.28 L0 1 L-0.28 0.28 L-1 0 L-0.28 -0.28 Z";

function useUid(): (name: string) => string {
  const base = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  return (name) => `rk${base}${name}`;
}

const EGG = "M50 4 C72 4 86 34 86 58 C86 80 70 96 50 96 C30 96 14 80 14 58 C14 34 28 4 50 4 Z";

export function RankStage({ k, layer, small }: { k: Key; layer: "back" | "front"; small: boolean }) {
  const id = useUid();
  const [core, edge] = TINT[k];
  if (layer === "front") {
    if (k === "egg") {
      return (
        <svg className="rkd rkd-stage rkd-front rkd-egg-front" viewBox="0 0 100 100" aria-hidden>
          <defs>
            <linearGradient id={id("eh")} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#fff" stopOpacity=".55" /><stop offset=".5" stopColor="#fff" stopOpacity="0" /></linearGradient>
          </defs>
          <path d={EGG} fill="none" stroke="rgba(230,255,244,.55)" strokeWidth="1.4" />
          <ellipse cx="36" cy="30" rx="9" ry="15" transform="rotate(-24 36 30)" fill={`url(#${id("eh")})`} />
          <g fill="rgba(210,255,236,.28)">
            <ellipse cx="62" cy="22" rx="2.2" ry="1.4" /><ellipse cx="72" cy="44" rx="1.6" ry="1.1" /><ellipse cx="26" cy="62" rx="2" ry="1.3" />
            <ellipse cx="70" cy="74" rx="2.4" ry="1.5" /><ellipse cx="40" cy="84" rx="1.5" ry="1" /><ellipse cx="22" cy="44" rx="1.2" ry=".9" />
          </g>
          <path className="rkd-crack" d="M58 9 L54 18 L60 24 L53 33 L58 38" fill="none" stroke="#5dffc0" strokeWidth="1.3" strokeLinejoin="round" strokeLinecap="round" />
        </svg>
      );
    }
    if (k === "chick") {
      return (
        <svg className="rkd rkd-stage rkd-front" viewBox="0 0 100 100" aria-hidden>
          <defs>
            <linearGradient id={id("cs")} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fffaf0" /><stop offset="1" stopColor="#e2cf9f" /></linearGradient>
          </defs>
          <g className="rkd-cup">
            <path d="M15 72 L22 65 L29 72 L36 64 L43 71 L50 63 L57 71 L64 64 L71 72 L78 65 L85 72 C85 89 70 98 50 98 C30 98 15 89 15 72 Z"
                  fill={`url(#${id("cs")})`} stroke="rgba(120,90,30,.45)" strokeWidth=".8" strokeLinejoin="round" />
            <path d="M22 80 C30 90 44 94 56 93" fill="none" stroke="#fff" strokeOpacity=".7" strokeWidth="1.6" strokeLinecap="round" />
            <g fill="rgba(140,105,40,.28)"><ellipse cx="66" cy="82" rx="2" ry="1.3" /><ellipse cx="38" cy="88" rx="1.5" ry="1" /><ellipse cx="74" cy="76" rx="1.2" ry=".8" /></g>
          </g>
        </svg>
      );
    }
    if (small || !SPARK_COLOR[k]) return null;
    const colors = SPARK_COLOR[k]!;
    const n = k === "kiwi" ? 4 : k === "mythic" || k === "legend" || k === "golden" ? 8 : 6;
    return (
      <svg className="rkd rkd-stage rkd-front" viewBox="0 0 100 100" aria-hidden>
        {SPARKS.slice(0, n).map(([x, y, s, d, t], i) => (
          <g key={i} transform={`translate(${x} ${y})`}>
            <g className="rkd-spark" style={{ animationDelay: `${d}s`, animationDuration: `${t}s` } as CSSProperties}>
              {k === "warrior" ? <circle r={s * 0.9} fill={colors[i % colors.length]} />
                : <path d={STAR} transform={`scale(${s * 1.6})`} fill={colors[i % colors.length]} />}
            </g>
          </g>
        ))}
      </svg>
    );
  }

  // back layer
  const rays = !small && (k === "golden" || k === "mythic" || k === "royal");
  return (
    <svg className="rkd rkd-stage rkd-back" viewBox="0 0 100 100" aria-hidden>
      <defs>
        <radialGradient id={id("bg")} cx="50%" cy="52%" r="50%">
          <stop offset="0" stopColor={core} stopOpacity={k === "mythic" ? 0.28 : 0.34} />
          <stop offset=".62" stopColor={edge} stopOpacity=".22" />
          <stop offset="1" stopColor={edge} stopOpacity="0" />
        </radialGradient>
        <linearGradient id={id("ray")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={k === "royal" ? "#e3c7ff" : k === "mythic" ? "#ffffff" : "#fff1a8"} stopOpacity="0" />
          <stop offset="1" stopColor={k === "royal" ? "#b78cff" : k === "mythic" ? "#bfe6ff" : "#ffd24d"} stopOpacity=".55" />
        </linearGradient>
        <linearGradient id={id("fl")} x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="#00e58f" stopOpacity=".85" /><stop offset=".6" stopColor="#29ffb0" stopOpacity=".45" /><stop offset="1" stopColor="#eafff6" stopOpacity="0" />
        </linearGradient>
        <radialGradient id={id("egg")} cx="42%" cy="36%" r="70%">
          <stop offset="0" stopColor="#eafff6" stopOpacity=".34" /><stop offset=".55" stopColor="#3fbf8f" stopOpacity=".18" /><stop offset="1" stopColor="#0b3b2b" stopOpacity=".55" />
        </radialGradient>
      </defs>
      {k !== "egg" && <circle className="rkd-disc" cx="50" cy="50" r="47" fill={`url(#${id("bg")})`} />}
      {k !== "egg" && !small && <circle className="rkd-ring" cx="50" cy="50" r="46.5" fill="none" stroke={core} strokeOpacity=".4" strokeWidth=".7" strokeDasharray={k === "mythic" ? "2 3" : undefined} />}
      {rays && (
        <g className="rkd-rays" fill={`url(#${id("ray")})`}>
          {Array.from({ length: 12 }, (_, i) => (
            <path key={i} d="M50 50 L46.5 3 L53.5 3 Z" transform={`rotate(${i * 30} 50 50)`}
                  fill={k === "mythic" ? ["#ff9ae6", "#9ad8ff", "#7dffc8", "#ffe066"][i % 4] : undefined} opacity={k === "mythic" ? 0.22 : undefined} />
          ))}
        </g>
      )}
      {k === "legend" && !small && (
        <g className="rkd-runes">
          {Array.from({ length: 16 }, (_, i) => (
            <path key={i} d="M50 1.2 L51.4 3.4 L50 5.6 L48.6 3.4 Z" transform={`rotate(${i * 22.5} 50 50)`} fill={i % 4 === 0 ? "#eafff6" : "#29ffb0"} opacity={i % 4 === 0 ? 1 : 0.7} />
          ))}
        </g>
      )}
      {k === "egg" && (
        <g className="rkd-egg-glow">
          <ellipse cx="50" cy="60" rx="40" ry="34" fill="#00e58f" opacity=".14" />
          <path d={EGG} fill={`url(#${id("egg")})`} />
        </g>
      )}
    </svg>
  );
}

const GOLD = (id: (n: string) => string) => (
  <linearGradient id={id("gold")} x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stopColor="#fff3b0" /><stop offset=".45" stopColor="#ffcc33" /><stop offset="1" stopColor="#b8780a" />
  </linearGradient>
);

function Crown({ id }: { id: (n: string) => string }) {
  return (
    <g className="rkd-crown" transform="rotate(9 655 100) translate(655 104) scale(1.55) translate(-655 -104)">
      <path d="M572 104 L560 8 L612 54 L655 -16 L698 54 L750 8 L738 104 Q655 120 572 104 Z" fill={`url(#${id("gold")})`} stroke="#8a5a00" strokeWidth="7" strokeLinejoin="round" />
      <path d="M574 86 Q655 102 736 86" fill="none" stroke="#fff3b0" strokeOpacity=".7" strokeWidth="6" />
      <circle cx="560" cy="8" r="13" fill="#fff3b0" /><circle cx="655" cy="-16" r="15" fill="#fff3b0" /><circle cx="750" cy="8" r="13" fill="#fff3b0" />
      <g className="rkd-gems">
        <ellipse cx="655" cy="72" rx="17" ry="20" fill="#e8304a" stroke="#7a0d1d" strokeWidth="4" />
        <ellipse cx="605" cy="80" rx="11" ry="13" fill="#3a8bff" stroke="#123f80" strokeWidth="4" />
        <ellipse cx="705" cy="80" rx="11" ry="13" fill="#00e58f" stroke="#00593a" strokeWidth="4" />
      </g>
      <g transform="translate(648 64) scale(26)"><path className="rkd-twinkle" d={STAR} fill="#fff" /></g>
    </g>
  );
}

export function RankGear({ k, layer }: { k: Key; layer: "back" | "front" }) {
  const id = useUid();
  let body: ReactNode = null;
  if (layer === "back") {
    if (k === "scout") {
      body = (
        <g className="rkd-pack">
          <defs><linearGradient id={id("bp")} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#c48a4a" /><stop offset="1" stopColor="#6e4420" /></linearGradient></defs>
          <rect x="-90" y="190" width="290" height="380" rx="70" fill={`url(#${id("bp")})`} stroke="#3e2410" strokeWidth="10" />
          <path d="M-88 300 Q55 250 198 300 L198 250 Q55 190 -88 250 Z" fill="#8f5c2c" stroke="#3e2410" strokeWidth="8" />
          <rect x="-50" y="400" width="150" height="110" rx="30" fill="#7b4c22" stroke="#3e2410" strokeWidth="8" />
          <rect x="-82" y="118" width="270" height="86" rx="43" fill="#3d7a3a" stroke="#1d3f1c" strokeWidth="8" />
          <path d="M-10 122 v80 M110 122 v80" stroke="#c48a4a" strokeWidth="14" />
        </g>
      );
    } else if (k === "warrior") {
      body = (
        <g className="rkd-sword" transform="translate(170 40) rotate(-38)">
          <defs><linearGradient id={id("bl")} x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="#9fb3c4" /><stop offset=".5" stopColor="#ffffff" /><stop offset="1" stopColor="#7b8d9c" /></linearGradient></defs>
          <path d="M-26 0 L26 0 L24 980 L0 1060 L-24 980 Z" fill={`url(#${id("bl")})`} stroke="#3c4a56" strokeWidth="7" />
          <path d="M0 10 V990" stroke="#5e7282" strokeWidth="6" />
          <rect x="-112" y="-34" width="224" height="40" rx="20" fill="#d9a520" stroke="#6b4a00" strokeWidth="7" />
          <rect x="-20" y="-190" width="40" height="160" rx="12" fill="#5a2a14" stroke="#2c1206" strokeWidth="6" />
          <path d="M-20 -170 L20 -150 M-20 -130 L20 -110 M-20 -90 L20 -70" stroke="#a0522d" strokeWidth="6" />
          <circle cy="-210" r="30" fill="#d9a520" stroke="#6b4a00" strokeWidth="7" />
          <g transform="translate(0 -14) scale(40)"><path className="rkd-twinkle" d={STAR} fill="#fff" /></g>
        </g>
      );
    } else if (k === "royal") {
      body = (
        <g className="rkd-cape">
          <defs><linearGradient id={id("cp")} x1="1" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#d1263f" /><stop offset="1" stopColor="#6a0f22" /></linearGradient></defs>
          <path d="M430 96 C250 10 40 110 -70 330 C-140 480 -170 640 -200 800 C-70 760 50 770 170 740 C110 600 190 300 430 96 Z"
                fill={`url(#${id("cp")})`} stroke="#ffcc33" strokeWidth="14" strokeLinejoin="round" />
          <path d="M150 120 C20 220 -60 420 -110 700 M300 70 C150 160 60 420 20 720" fill="none" stroke="#4a0816" strokeOpacity=".55" strokeWidth="12" strokeLinecap="round" />
          <path d="M-200 800 C-70 760 50 770 170 740" fill="none" stroke="#fff6e8" strokeWidth="40" strokeLinecap="round" />
          <g fill="#1a1a1a">{[-160, -100, -40, 20, 80, 140].map((x, i) => <path key={i} d={`M${x} ${786 - (x + 200) * 0.13} l-6 -14 l6 4 l6 -4 Z`} />)}</g>
        </g>
      );
    }
  } else {
    if (k === "kiwi") {
      // a little silver fern pinned to the chest
      body = (
        <g className="rkd-fern" transform="translate(560 470) rotate(-28)">
          <circle r="78" fill="#062b1d" stroke="#00e58f" strokeWidth="9" />
          <path d="M0 52 C4 18 2 -20 -6 -52" fill="none" stroke="#dffff0" strokeWidth="8" strokeLinecap="round" />
          <g fill="#dffff0">
            {[-40, -26, -12, 2, 16, 30].map((y, i) => {
              const w = 30 - i * 3;
              return <g key={i}><ellipse cx={-w / 2 - 3} cy={y} rx={w / 2} ry="6" transform={`rotate(-28 ${-w / 2 - 3} ${y})`} />
                <ellipse cx={w / 2 + 1} cy={y - 4} rx={w / 2} ry="6" transform={`rotate(28 ${w / 2 + 1} ${y - 4})`} /></g>;
            })}
          </g>
        </g>
      );
    } else if (k === "chick") {
      body = (
        <g className="rkd-cap" transform="rotate(-14 650 80)">
          <path d="M556 124 L586 100 L612 126 L640 98 L666 124 L694 98 L722 122 L752 104 C748 36 700 4 652 6 C600 8 560 50 556 124 Z"
                fill="#fffaf0" stroke="rgba(120,90,30,.55)" strokeWidth="7" strokeLinejoin="round" />
          <path d="M600 40 C620 24 650 20 672 24" fill="none" stroke="#fff" strokeWidth="9" strokeLinecap="round" />
        </g>
      );
    } else if (k === "scout") {
      body = (
        <g>
          <path d="M240 160 C300 330 300 470 330 610" fill="none" stroke="#7b4c22" strokeWidth="38" strokeLinecap="round" />
          <path d="M240 160 C300 330 300 470 330 610" fill="none" stroke="#c48a4a" strokeWidth="10" strokeDasharray="22 18" />
          <circle cx="298" cy="400" r="34" fill="#3d7a3a" stroke="#ffd24d" strokeWidth="7" />
          <path d="M298 380 L305 396 L322 398 L309 409 L313 426 L298 417 L283 426 L287 409 L274 398 L291 396 Z" fill="#ffd24d" />
        </g>
      );
    } else if (k === "warrior") {
      body = (
        <g className="rkd-band">
          <path className="rkd-tail" d="M500 112 C440 70 400 60 340 70 C380 92 420 104 470 128 Z" fill="#c4241a" />
          <path className="rkd-tail rkd-tail-2" d="M500 116 C450 140 420 170 380 200 C430 196 470 170 510 140 Z" fill="#a51d14" />
          <path d="M498 96 C580 108 660 128 740 146 L736 178 C656 160 576 142 494 130 Z" fill="#e63a2e" stroke="#7a1208" strokeWidth="6" strokeLinejoin="round" />
          <path d="M500 106 C580 118 660 138 738 154" fill="none" stroke="#ff8a7a" strokeWidth="5" strokeOpacity=".7" />
          <circle cx="500" cy="114" r="22" fill="#c4241a" stroke="#7a1208" strokeWidth="6" />
        </g>
      );
    } else if (k === "knight") {
      body = (
        <g className="rkd-shield" transform="rotate(-8 300 490)">
          <defs>
            <linearGradient id={id("sh")} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#e9f1fa" /><stop offset=".45" stopColor="#9fb3c8" /><stop offset="1" stopColor="#4d6178" /></linearGradient>
            <clipPath id={id("shc")}><path d="M300 300 C400 300 450 322 450 322 L450 452 C450 562 380 632 300 682 C220 632 150 562 150 452 L150 322 C150 322 200 300 300 300 Z" /></clipPath>
          </defs>
          <path d="M300 300 C400 300 450 322 450 322 L450 452 C450 562 380 632 300 682 C220 632 150 562 150 452 L150 322 C150 322 200 300 300 300 Z"
                fill={`url(#${id("sh")})`} stroke="#2a3a4c" strokeWidth="12" strokeLinejoin="round" />
          <path d="M300 330 C380 330 420 346 420 346 L420 452 C420 545 362 604 300 646 C238 604 180 545 180 452 L180 346 C180 346 220 330 300 330 Z"
                fill="#1d3f7a" stroke="#d9a520" strokeWidth="8" />
          <path d="M240 420 L300 480 L360 420 L360 470 L300 530 L240 470 Z" fill="#00e58f" stroke="#00593a" strokeWidth="6" strokeLinejoin="round" />
          <g fill="#d9a520">{[[205, 360], [395, 360], [300, 610], [190, 470], [410, 470]].map(([x, y], i) => <circle key={i} cx={x} cy={y} r="9" />)}</g>
          <g clipPath={`url(#${id("shc")})`}><g transform="rotate(20 95 500)"><rect className="rkd-sheen" x="60" y="250" width="70" height="500" fill="#fff" opacity=".55" /></g></g>
        </g>
      );
    } else if (k === "royal") {
      body = <><defs>{GOLD(id)}</defs><Crown id={id} /></>;
    } else if (k === "golden") {
      const leaves = Array.from({ length: 13 }, (_, i) => {
        const a = (192 + i * (156 / 12)) * (Math.PI / 180);
        const x = 655 + 175 * Math.cos(a), y = 225 + 175 * Math.sin(a);
        return <ellipse key={i} cx={x} cy={y} rx="56" ry="22" transform={`rotate(${(a * 180) / Math.PI + (i < 6 ? 60 : i > 6 ? 120 : 90)} ${x} ${y})`} />;
      });
      body = (
        <g className="rkd-laurel">
          <defs>{GOLD(id)}</defs>
          <g fill={`url(#${id("gold")})`} stroke="#8a5a00" strokeWidth="5">{leaves}</g>
          <circle cx="655" cy="50" r="26" fill="#e8304a" stroke="#7a0d1d" strokeWidth="6" />
        </g>
      );
    } else if (k === "legend") {
      body = (
        <g className="rkd-orbit">
          <ellipse cx="500" cy="440" rx="560" ry="150" fill="none" stroke="#29ffb0" strokeOpacity=".35" strokeWidth="8" strokeDasharray="30 26" transform="rotate(-12 500 440)" />
        </g>
      );
    } else if (k === "mythic") {
      body = (
        <g className="rkd-halo">
          <ellipse cx="640" cy="-6" rx="130" ry="30" fill="none" stroke="#fff3b0" strokeWidth="26" opacity=".35" />
          <ellipse cx="640" cy="-6" rx="130" ry="30" fill="none" stroke="#ffe066" strokeWidth="12" />
          <ellipse cx="640" cy="-10" rx="112" ry="20" fill="none" stroke="#fff" strokeWidth="4" opacity=".8" />
        </g>
      );
    }
  }
  if (!body) return null;
  return <svg className={`rkd rkd-gear rkd-gear-${layer}`} viewBox="0 0 1000 844" aria-hidden>{body}</svg>;
}

/** The mythic Kiwi's holographic shimmer: a rainbow sheen masked to the Kiwi's own outline. */
export function HoloSheen({ src }: { src: string }) {
  const mask = `url(${src})`;
  return <span className="rkd-holo" style={{ WebkitMaskImage: mask, maskImage: mask }} aria-hidden><i /></span>;
}

/** Legend / Mythic: a soft glow in the Kiwi's own outline, breathing behind it. */
export function AuraGlow({ src, k }: { src: string; k: Key }) {
  const mask = `url(${src})`;
  return (
    <span className={`rkd-aura rkd-aura-${k}`} aria-hidden>
      <span style={{ WebkitMaskImage: mask, maskImage: mask }} />
    </span>
  );
}
