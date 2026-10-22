/**
 * Invite friends: the member's own link, one big copy button ("Copied 🥝"), and what invites have earned so far.
 * Plain about the rule: a click earns nothing; XP comes when the friend joins AND does one real thing.
 */
import { useEffect, useRef, useState } from "react";
import { memberApi, type ReferralInfo } from "./members";
import { burst, pop } from "./fx";
import "./play.css";

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const t = document.createElement("textarea");
      t.value = text;
      t.setAttribute("readonly", "");
      t.style.position = "fixed";
      t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      const ok = document.execCommand("copy");
      t.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

export function useReferrals(): ReferralInfo | null | undefined {
  const [info, setInfo] = useState<ReferralInfo | null | undefined>(undefined);
  useEffect(() => {
    void memberApi<ReferralInfo>("/members/me/referrals").then(setInfo).catch(() => setInfo(null));
  }, []);
  return info;
}

export function InvitePanel({ info }: { info: ReferralInfo }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (copied === "idle") return;
    const t = window.setTimeout(() => setCopied("idle"), 1800);
    return () => window.clearTimeout(t);
  }, [copied]);
  async function copy() {
    const ok = await copyText(info.link);
    setCopied(ok ? "copied" : "failed");
    if (ok && btn.current) {
      burst(btn.current, 8, 44);
      pop(btn.current, 1.05);
    }
  }
  return (
    <div className="iv">
      <p className="iv-lede">
        {info.reward_xp > 0 ? <>Get <strong>+{info.reward_xp} XP</strong> for every friend who joins and does one real thing in Price Chat.</> : "Invite friends to Price Chat."}
        {info.welcome_xp > 0 ? <> They get <strong>+{info.welcome_xp} XP</strong> too.</> : null}
      </p>
      <div className="iv-link">
        <input readOnly value={info.link} aria-label="Your invite link" onFocus={(e) => e.currentTarget.select()} />
      </div>
      <button ref={btn} type="button" className={`pp-go${copied === "copied" ? " pp-go-done" : ""}`} onClick={() => void copy()}>
        {copied === "copied" ? <><svg className="pp-go-check" viewBox="0 0 16 16" aria-hidden><path d="M3 8.5l3.2 3.2L13 4.8" /></svg> Copied 🥝</>
          : "Copy invite link"}
      </button>
      {copied === "failed" && <p className="pp-problem" role="alert">Couldn't copy. Select the link above and copy it.</p>}
      <dl className="iv-stats" aria-label="Your referrals">
        <div><dt>Joined</dt><dd>{info.joined}</dd></div>
        <div><dt>Qualified</dt><dd>{info.qualified}</dd></div>
        <div><dt>XP earned</dt><dd>{info.xp_earned}</dd></div>
      </dl>
      <p className="iv-fine">
        Opening the link earns nothing. A friend counts once, after they create their Kiwi Passport
        {info.qualifying.length > 0 ? <> and then do one of: {info.qualifying.join(", ").toLowerCase()}</> : null}. Inviting yourself never counts.
      </p>
    </div>
  );
}

/** A small dialog over the Passport. Esc, the backdrop or Close dismisses it. */
export function InviteModal({ onClose }: { onClose: () => void }) {
  const info = useReferrals();
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="iv-modal" role="dialog" aria-modal="true" aria-labelledby="invite-title" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="iv-card">
        <div className="iv-head">
          <h2 className="site-display iv-title" id="invite-title">👥 Invite friends</h2>
          <button type="button" className="site-textbutton iv-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {info === undefined ? <p className="site-empty">Loading…</p>
          : info === null ? <p className="pp-problem">Your invite link isn't available right now.</p>
          : !info.enabled ? <p className="site-note">Invites are switched off for now.</p>
          : <InvitePanel info={info} />}
      </div>
    </div>
  );
}
