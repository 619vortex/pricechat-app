import { useEffect, useState } from "react";
import { reducedMotion } from "./motion";
import "./kiwibrain.css";

export const BRAIN_PATH = "/earn/brain";
const FOUND_KEY = "pc-kiwi-brain-found";

function found(): boolean {
  try {
    return localStorage.getItem(FOUND_KEY) === "1";
  } catch {
    return false;
  }
}

/** The Kiwi from behind (the joke): black round body, tail tuft, green Price Chat band, legs. Now and then it wiggles,
 *  bounces, and looks back over its shoulder (big eye, long curved beak) and blinks. Pure SVG + CSS, no assets. */
export function KiwiBack({ size = 92 }: { size?: number }) {
  return (
    <svg className="kb-back" viewBox="0 0 120 120" width={size} height={size} aria-hidden>
      <ellipse className="kb-glow" cx="60" cy="108" rx="40" ry="9" />
      <ellipse className="kb-shadow" cx="60" cy="110" rx="30" ry="5" />
      <g className="kb-legs">
        <path d="M48 92 L45 108 M45 108 l-6 2 M45 108 l3 3" />
        <path d="M72 92 L75 108 M75 108 l6 2 M75 108 l-3 3" />
      </g>
      <g className="kb-body">
        {/* the head, turning to look back over the shoulder (hidden behind the body most of the time) */}
        <g className="kb-head">
          <circle cx="86" cy="40" r="17" fill="#161816" stroke="rgba(0,229,143,0.35)" strokeWidth="1.5" />
          <path d="M100 44 C 112 50, 116 62, 114 74" stroke="#e8c37a" strokeWidth="4" fill="none" strokeLinecap="round" />
          <circle cx="90" cy="37" r="7.5" fill="#fff" />
          <circle className="kb-pupil" cx="92" cy="38" r="3.6" fill="#0b0b0b" />
          <rect className="kb-lid" x="82" y="29" width="16" height="0" fill="#141414" />
        </g>
        <ellipse cx="60" cy="66" rx="36" ry="32" fill="#161816" stroke="rgba(0,229,143,0.35)" strokeWidth="1.5" />
        <path d="M60 84 q-1 8 0 12" stroke="#2c2f2d" strokeWidth="2" fill="none" strokeLinecap="round" />
        <path d="M30 56 Q 60 44 90 56" stroke="#00e58f" strokeWidth="4" fill="none" strokeLinecap="round" />
        {/* feather texture + the butt (the clickable joke) */}
        <path d="M40 76 q4 4 8 0 M54 84 q4 4 8 0 M68 76 q4 4 8 0 M46 64 q4 4 8 0 M62 64 q4 4 8 0" stroke="#2a2a2a" strokeWidth="2" fill="none" strokeLinecap="round" />
        <g className="kb-tail">
          <path d="M56 92 q4 10 0 14 M60 93 q2 10 4 13 M64 92 q-1 9 5 11" stroke="#1d1d1d" strokeWidth="3" fill="none" strokeLinecap="round" />
        </g>
      </g>
    </svg>
  );
}

/** The secret entrance on the trading dashboard. Discovery, not security: the Brain's data is behind the member login. */
export function KiwiButt() {
  const [reveal, setReveal] = useState(false);
  const [known, setKnown] = useState(found);
  useEffect(() => {
    if (!reveal) return;
    try {
      localStorage.setItem(FOUND_KEY, "1");
    } catch {
      /* storage blocked: the URL still works */
    }
    setKnown(true);
    const id = window.setTimeout(() => window.location.assign(BRAIN_PATH), reducedMotion() ? 400 : 1700);
    return () => window.clearTimeout(id);
  }, [reveal]);
  return (
    <div className="kb-egg">
      {/* no label: you find out by tapping it (once found, it goes straight there) */}
      <button className="kb-butt" onClick={() => (known ? window.location.assign(BRAIN_PATH) : setReveal(true))}
              aria-label={known ? "Kiwi Brain" : "A kiwi, facing away"}>
        <KiwiBack />
      </button>
      {reveal && (
        <div className="kb-reveal" role="status">
          <div className="kb-reveal-in">
            <span className="kb-reveal-pop">🥝</span>
            <p>YOU FOUND THE KIWI'S BRAIN 🥝</p>
          </div>
        </div>
      )}
    </div>
  );
}
