import { useCallback, useEffect, useRef, useState } from "react";
import { SitePage } from "./community";
import { openLogin } from "./emailLogin";
import { burst } from "./fx";
import { memberApi, trackedRef, type RankArt } from "./members";
import { reducedMotion } from "./motion";
import type { Sound } from "./sound";
import { SignalToasts } from "./brain/toasts";
import { KiwiButt } from "./kiwibutt";
import { KiwiKitchen } from "./kitchen";
import { RankKiwi } from "./RankKiwi";
import { ConnectDialog, TradingTerminal, type Connection } from "./terminal";
import { PnlCard, type Pnl } from "./pnl";
import { EarnLeaderboard } from "./earnboard";
import { EarnTabs } from "./arbitrage";
import type { Tiers } from "./tiers";
import "./tradingbot.css";

/** VIP Trading Bot access funnel (/trading-bot). Every result shown here comes from the server, which decides rank,
 *  wallet ownership, referral and deposit from the database and Hyperliquid's own records. This page only asks. */

type Step = "todo" | "done" | "locked" | "failed" | "review";
type Stage = "LOCKED" | "IN_PROGRESS" | "PENDING_VERIFICATION" | "ELIGIBLE" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "REVOKED";
interface RankOut { level: number; key: string; name: string; min_xp: number; art: RankArt & { asset?: string | null } }
interface BotState {
  open: boolean;
  preview: boolean;
  signed_in: boolean;
  stage: Stage;
  journey: number;
  rules: { min_rank_level: number; min_deposit_usd: number; referral_code: string; referral_url: string; telegram_ready: boolean };
  steps: Record<"passport" | "rank" | "hyperliquid" | "wallet" | "deposit" | "telegram" | "apply", Step>;
  member?: { username: string; id: string };
  rank?: { current: RankOut; required: RankOut; ok: boolean; xp: number; score: number; score_needed: number; remaining: number; progress: number };
  wallet?: { address: string; checked_at: string | null; referral: string; referral_code_seen: string | null; deposit: string; deposit_usd: number; other_inflow_usd: number; account_value?: number | null } | null;
  telegram?: { linked: boolean; username?: string | null };
  application?: { id: string; status: Stage; created_at: string; manual_review: boolean; joined: boolean; has_invite: boolean; message: string | null } | null;
  can_apply?: boolean;
  wallet_xp?: number;
  tiers?: Tiers;
  access?: { ready: boolean; group: boolean; bot: boolean; group_url: string | null };
  flow?: { hyperliquid: boolean; first_trade: boolean; has_wallet: boolean };
  trading?: { api: boolean; paper: boolean; live: boolean };
  bot?: { username: string; track_record: boolean };
  apply_blocker?: string | null;
}


const JOURNEY = ["Passport", "Hyperliquid", "Telegram", "Bot", "Trading API"];

const track = (kind: "cta" | "ref_click") => {
  void memberApi("/members/trading-bot/event", { method: "POST", body: JSON.stringify({ kind }) }).catch(() => undefined);
};

function Check({ state }: { state: Step }) {
  return (
    <span className={`tb-check tb-check-${state}`} aria-hidden>
      {state === "done" ? (
        <svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
      ) : state === "failed" ? "✕" : state === "review" ? "?" : (
        <svg viewBox="0 0 24 24" className="tb-lock"><path className="tb-shackle" d="M8 11V8a4 4 0 0 1 8 0v3" /><rect x="5.5" y="11" width="13" height="9" rx="2" /></svg>
      )}
    </span>
  );
}

function StepCard({ n, title, state, children, flash }: { n: number; title: string; state: Step; children: React.ReactNode; flash?: boolean }) {
  return (
    <section id={`tb-step-${n}`} className={`tb-step tb-step-${state}${flash ? " is-flash" : ""}`} aria-label={`Step ${n}: ${title}`}>
      <header className="tb-step-head">
        <Check state={state} />
        <span className="tb-step-n">Step {n}</span>
        <h2>{title}</h2>
        <span className={`tb-chip tb-chip-${state}`}>{{ done: "Done", todo: "Your move", locked: "Locked", failed: "Not met", review: "Manual review" }[state]}</span>
      </header>
      <div className="tb-step-body">{children}</div>
    </section>
  );
}

function Particles() {
  if (reducedMotion()) return null;
  return (
    <div className="tb-particles" aria-hidden>
      {Array.from({ length: 14 }, (_, i) => <i key={i} style={{ ["--i" as string]: i }} />)}
    </div>
  );
}

function VipCard({ s }: { s: BotState }) {
  const approved = s.stage === "APPROVED";
  return (
    <div className={`tb-card${approved ? " is-approved" : ""}`} aria-label={approved ? "VIP membership card: approved" : "VIP membership card: locked"}>
      <div className="tb-card-shine" aria-hidden />
      <div className="tb-card-top"><span className="tb-card-brand">PRICE CHAT</span><span className="tb-card-tag">{approved ? "VIP · ACTIVE" : "VIP · LOCKED"}</span></div>
      <div className="tb-card-chip" aria-hidden />
      <div className="tb-card-name">{s.member?.username ?? "Your name here"}</div>
      <div className="tb-card-bottom"><span>Kiwi Trading Bot</span><span>{s.rank ? `Kiwi Rank ${s.rank.current.level}` : "Kiwi Passport"}</span></div>
    </div>
  );
}

/** How many of the 5 one-time steps are done (Kiwi Rank, Hyperliquid, Telegram, Bot, First trade). */
function journeyOf(s: BotState): number {
  return [s.rank?.ok, s.flow?.hyperliquid, s.access?.group, s.access?.bot, s.flow?.first_trade || s.trading?.api || s.trading?.paper].filter(Boolean).length;
}

export function TradingBotPage() {
  const [s, setS] = useState<BotState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  const [party, setParty] = useState(false);  // balloons + fanfare when the bot gets connected
  const prev = useRef<BotState | null>(null);
  const [record, setRecord] = useState<{ configured: boolean; bot?: string; address?: string; pnl?: Pnl | null } | null>(null);
  useEffect(() => { void memberApi<NonNullable<typeof record>>("/members/trading-bot/track-record").then(setRecord).catch(() => undefined); }, []);
  const stepsRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  const show = useCallback((next: BotState) => {
    const p = prev.current;
    if (p && p.signed_in) {  // celebrate what just changed (only real server results)
      const done = (k: keyof BotState["steps"]) => p.steps[k] !== "done" && next.steps[k] === "done";
      // what just happened → message, sound, and the step card it belongs to (only real server results)
      const hit: [string, Sound, number] | null = next.stage === "APPROVED" && p.stage !== "APPROVED" ? ["🎉 You're in. Welcome to the bot group.", "chase", 4]
        : done("wallet") ? [`Wallet connected ✓${next.wallet_xp ? ` +${next.wallet_xp} XP` : ""}`, "coin", 3] : done("hyperliquid") ? ["Referral confirmed by Hyperliquid ✓", "shine", 2]
        : done("deposit") ? ["Deposit verified ✓", "cash", 3] : done("apply") ? ["Application sent ✓", "swish", 4]
        : done("rank") ? [`Kiwi Rank ${next.rules.min_rank_level} reached ✓`, "levelup", 1]
        : next.tiers?.current && next.tiers.current !== p.tiers?.current ? [`${next.tiers.current} tier reached! +${next.tiers.tiers.find((t) => t.name === next.tiers!.current)?.xp ?? 0} XP`, "cash", 3]
        : null;
      if (hit) {
        const [msg, sound, step] = hit;
        setToast(msg);
        window.setTimeout(() => setToast(null), 3200);
        void sound;  // Earn is quiet except when a signal opens or closes (owner's call, 2026-10-18)
        setFlash(step);
        window.setTimeout(() => setFlash(null), 1600);
        if (!reducedMotion()) {
          const at = next.stage === "APPROVED" ? cardRef.current : document.getElementById(`tb-step-${step}`);
          window.setTimeout(() => { if (at) burst(at, next.stage === "APPROVED" ? 28 : 14, next.stage === "APPROVED" ? 160 : 90, true); }, 60);
        }
      }
    }
    prev.current = next;
    setS(next);
  }, []);

  const load = useCallback(async () => {
    try {
      show(await memberApi<BotState>("/members/trading-bot"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't load.");
    }
  }, [show]);
  useEffect(() => { void load(); }, [load]);
  // Hyperliquid can't send people back here, so when this page becomes visible again after they left for Hyperliquid,
  // it re-reads Hyperliquid by itself: the referral / deposit steps turn Done (with their sound) without pressing anything.
  const away = useRef(false);
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible" || !away.current) return;
      away.current = false;
      const cur = prev.current;
      if (cur?.wallet) void memberApi<BotState>("/members/trading-bot/wallet/recheck", { method: "POST" }).then(show).catch(() => undefined);
      else void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => { document.removeEventListener("visibilitychange", onVisible); window.removeEventListener("focus", onVisible); };
  }, [load, show]);
  const leaving = () => { away.current = true; };

  async function act(key: string, fn: () => Promise<BotState | void>) {
    setBusy(key);
    setErr(null);
    try {
      const out = await fn();
      if (out) show(out);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }




  async function checkGroup() {
    await act("group", async () => {
      const r = await memberApi<{ done: boolean; xp: number; message?: string; state: BotState }>("/members/trading-bot/access/group", { method: "POST" });
      if (!r.done) throw new Error(r.message ?? "Not in the group yet.");
      setToast(`Joined the group ✓${r.xp ? ` +${r.xp} XP` : ""}`);
      setFlash(4);
      const at = document.getElementById("tb-step-4");
      if (at && !reducedMotion()) burst(at, 18, 110, true);
      window.setTimeout(() => setToast(null), 3200);
      return r.state;
    });
  }

  const [connectOpen, setConnectOpen] = useState(false);
  // after "Connect the bot" opens Telegram: watch for the Start press (the bot records it) and finish the step by itself
  const [watchBot, setWatchBot] = useState(false);
  useEffect(() => {
    if (!watchBot) return;
    let n = 0;
    const id = window.setInterval(() => {
      n += 1;
      void memberApi<BotState>("/members/trading-bot").then((next) => {
        show(next);
        if (next.access?.bot) {
          setWatchBot(false);
          setToast("Bot connected ✓");
          setParty(true);
          window.setTimeout(() => setParty(false), 4200);
          window.setTimeout(() => setToast(null), 3200);
        }
      }).catch(() => undefined);
      if (n >= 60) setWatchBot(false);  // three minutes, then the step just waits for a reload
    }, 3000);
    return () => window.clearInterval(id);
  }, [watchBot, show]);

  function reward(msg: string, xp: number, sound: "coin" | "levelup" = "coin") {
    setToast(`${msg}${xp ? ` +${xp} XP` : ""}`);
    window.setTimeout(() => setToast(null), 3200);
    void sound;  // quiet: only signal open / close sounds on Earn
    const at = stepsRef.current;
    if (at && !reducedMotion()) burst(at, 18, 120, true);
  }

  async function joinedHyperliquid() {
    try {
      const r = await memberApi<{ xp: number; state: BotState }>("/members/trading-bot/hyperliquid/joined", { method: "POST" });
      prev.current = r.state;
      setS(r.state);
      if (r.xp) reward("Joined Hyperliquid ✓", r.xp);
    } catch { /* the link still opened; they can come back */ }
  }

  async function usePaper() {
    await act("paper", async () => {
      await memberApi("/members/trading/paper", { method: "POST" });
      return memberApi<BotState>("/members/trading-bot");
    });
  }

  const start = () => {
    track("cta");
    if (s && !s.signed_in) openLogin();
    else stepsRef.current?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  };

  const usable = !!s && (s.open || s.preview);
  const noRank = !s?.rules || s.rules.min_rank_level <= 1;  // the rank gate is off (since 2026-10-19 every member may use Earn)
  // Connected trader: onboarding is done, so the page stops selling access and becomes the trading terminal
  // (eligibility lost later shows a restricted terminal: positions, controls and history are never hidden).
  const connected = !!s && s.signed_in && (!!s.flow?.first_trade || (!!s.access?.bot && (!!s.trading?.api || !!s.trading?.paper)));
  const record_ = record?.configured && (
    <section className="tb-record" aria-label="Bot track record">
      <PnlCard title={`@${record.bot} track record`} pnl={record.pnl}
               sub={<>Live from Hyperliquid · <a href={`https://app.hyperliquid.xyz/explorer/address/${record.address}`} target="_blank" rel="noopener noreferrer">verify on-chain ↗</a> · past results don't guarantee future ones</>} />
    </section>
  );
  const waiting = false;  // (the old "waiting for a first trade" screen is now step 4: connect the trading API or use paper)
  if (s && connected) {
    const conn: Connection = { rankLevel: s.rank?.current.level ?? null, rankOk: !!s.rank?.ok, minRank: s.rules.min_rank_level,
      wallet: s.wallet?.address ?? null, referral: !!s.flow?.hyperliquid || s.steps.hyperliquid === "done", group: !!s.access?.group,
      bot: !!s.access?.bot, botUsername: s.bot?.username ?? "cryptopricechatbot", groupUrl: s.access?.group_url ?? "https://t.me/botpricechat" };
    return (
      <SitePage title="Kiwi trading bot" description="Kiwi's trading signals, your Hyperliquid positions and P&L, in one place." path="/earn">
        <div className="tb">
          <EarnTabs current="bot" />
          <TradingTerminal conn={conn} />
          <EarnLeaderboard />
          {/* the two Kiwis, animation only: tap one to find out what it is */}
          <div className="kk-row"><KiwiKitchen /><KiwiButt /></div>
          <SignalToasts onOpen={() => window.location.assign("/earn/brain#timemachine")} />
          <p className="tb-disclose">Trading is risky and not financial advice. Funds stay in your Hyperliquid account; Price Chat never sends withdrawals or transfers.</p>
        </div>
      </SitePage>
    );
  }
  return (
    <SitePage title="Earn: Kiwi Trading Bot" description="Free for every Kiwi Passport member: connect Hyperliquid and start in paper trading." path="/earn">
      <div className="tb">
        <EarnTabs current="bot" />
        {!waiting && !s?.access?.bot && <section className="tb-hero">
          <Particles />
          <div className="tb-hero-copy">
            <span className="tb-kicker">{noRank ? "Free · Every Kiwi Passport member" : "🔒 Members only"}</span>
            <h1>Meet the Kiwi <span>trading bot</span></h1>
            <p>{noRank ? "Sign in, connect Hyperliquid and start in paper trading. Going live is always your choice."
              : "Level up your Kiwi Passport, connect Hyperliquid and start in paper trading."}</p>
            {usable ? (
              <button className="tb-cta" onClick={start}>{s?.signed_in ? "Get started" : "Sign in to start"}</button>
            ) : s && (
              <p className="tb-soon" role="status">Opening soon. Keep leveling up your Kiwi in the meantime.</p>
            )}
            {s?.preview && <p className="tb-preview" role="note">Owner preview: the funnel is closed to the public.</p>}
          </div>
          <div className="tb-hero-art" ref={cardRef}>
            <div className="tb-kiwi">{s?.rank ? <RankKiwi art={s.rank.current.art} rankKey={s.rank.current.key} size={170} /> : <RankKiwi art={{ treatment: "none", overlay: null, placeholder: null, slot: null }} size={170} />}</div>
            {s && <VipCard s={s} />}
          </div>
        </section>}

        {s && !waiting && (
          <ol className="tb-journey" aria-label={`Progress: ${JOURNEY[Math.min(4, journeyOf(s))]}`}>
            <span className="tb-journey-fill" style={{ ["--p" as string]: journeyOf(s) / (JOURNEY.length - 1) }} aria-hidden />
            {JOURNEY.map((j, i) => (
              <li key={j} className={i < journeyOf(s) ? "is-done" : i === journeyOf(s) ? "is-now" : ""} aria-current={i === journeyOf(s) ? "step" : undefined}>
                <span className="tb-dot" aria-hidden>{i < journeyOf(s) ? "✓" : i + 1}</span>{j}
              </li>
            ))}
          </ol>
        )}

        {err && <p className="site-error tb-err" role="alert">{err}</p>}
        {toast && <p className="tb-toast" role="status">{toast}</p>}
        {party && !reducedMotion() && (
          <div className="tb-balloons" aria-hidden>
            {Array.from({ length: 16 }, (_, i) => <i key={i} style={{ ["--i" as string]: i }}>{["🎈", "🎉", "🎈", "🥳"][i % 4]}</i>)}
          </div>
        )}
        {!s && !err && <div className="tb-skel" aria-busy="true" />}

        {s && usable && !s.flow?.first_trade && !waiting && (
          <div className="tb-steps" ref={stepsRef}>
            {s.signed_in && (
              <ul className="tb-done-row" aria-label="Done">
                {s.rank?.ok && <li>{noRank ? "✓ Signed in" : `✓ Kiwi Rank ${s.rules.min_rank_level}`}</li>}
                {s.flow?.hyperliquid && <li>✓ Hyperliquid</li>}
                {s.access?.group && <li>✓ Telegram</li>}
                {s.access?.bot && <li>✓ Bot connected</li>}
              </ul>
            )}

            {(!s.signed_in || !s.rank?.ok) && (
              <StepCard n={1} flash={flash === 1} title={noRank ? "Sign in" : `🔒 ${s.rank?.required.name ?? "Scout Kiwi"} · Kiwi Rank ${s.rules.min_rank_level}`} state="todo">
                {/* when the owner sets a rank above 1, the server refuses every Earn step below it; this only explains why */}
                {noRank ? (
                  <p className="tb-unlock"><b>Your Kiwi Trading Bot</b>
                    <span>Free with a Kiwi Passport: onboarding, paper trading and connecting your Hyperliquid API wallet.</span></p>
                ) : (
                  <p className="tb-unlock"><b>UNLOCK EARN — Your Kiwi Trading Bot</b>
                    <span>Reaching {s.rank?.required.name ?? "Scout Kiwi"} unlocks the trading bot: onboarding, paper trading and connecting your Hyperliquid API wallet.</span></p>
                )}
                {!s.signed_in ? (
                  <button className="tb-btn" onClick={() => { track("cta"); openLogin(); }}>Sign in</button>
                ) : s.rank && (
                  <>
                    <div className="tb-rank">
                      <div className="tb-rank-badge"><RankKiwi art={s.rank.current.art} rankKey={s.rank.current.key} size={48} /></div>
                      <p className="tb-line"><b>{s.rank.current.name}</b> · {s.rank.score.toLocaleString()} / {s.rank.score_needed.toLocaleString()} XP · {s.rank.remaining.toLocaleString()} XP to go</p>
                    </div>
                    <div className="tb-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(s.rank.progress * 100)}
                         aria-label="Rank progress"><i style={{ ["--p" as string]: s.rank.progress }} /></div>
                    <a className="tb-btn" href="/passport">Level up my Kiwi</a>
                  </>
                )}
              </StepCard>
            )}

            {s.rank?.ok && !s.flow?.hyperliquid && (
              <StepCard n={2} flash={flash === 2} title="Join Hyperliquid" state="todo">
                <p className="tb-line">Sign up with our link · <b className="tb-ok">+25 XP</b></p>
                <a className="tb-cta tb-launch" href={trackedRef(s.rules.referral_url, "trading_bot")} target="_blank" rel="noopener noreferrer sponsored"
                   onClick={(e) => { leaving(); const b = e.currentTarget; b.classList.remove("is-go"); void b.offsetWidth; b.classList.add("is-go");
                     if (!reducedMotion()) burst(b, 12, 70, true); void joinedHyperliquid(); }}>
                  Open Hyperliquid ↗
                </a>
              </StepCard>
            )}

            {s.access?.ready && !s.access.bot && (
              <StepCard n={3} flash={flash === 3} title={s.access.group ? "Connect the bot" : "Join Telegram"} state="todo">
                {!s.access.group ? (
                  <>
                    <p className="tb-line">Join the bot group · <b className="tb-ok">+XP</b></p>
                    <div className="tb-row">
                      <a className="tb-cta" href={s.access.group_url ?? "https://t.me/botpricechat"} target="_blank" rel="noopener noreferrer"
                         onClick={() => { leaving(); }}>Join Telegram ↗</a>
                      <button className="tb-btn tb-btn-ghost" disabled={busy !== null} onClick={checkGroup}>{busy === "group" ? "…" : "I joined"}</button>
                    </div>
                  </>
                ) : (
                  <>
                    <p className="tb-line">Open <b>@{s.bot?.username ?? "cryptopricechatbot"}</b> and press <b>Start</b>: it links the bot to your Kiwi Passport.</p>
                    <a className="tb-cta" href={`https://t.me/${s.bot?.username ?? "cryptopricechatbot"}`} target="_blank" rel="noopener noreferrer"
                       onClick={() => { leaving(); setWatchBot(true); }}>
                      Connect the bot ↗</a>
                    {watchBot && <p className="tb-sub" role="status">Waiting for you to press Start in Telegram… this updates by itself.</p>}
                  </>
                )}
              </StepCard>
            )}

            {s.access?.bot && !s.trading?.api && !s.trading?.paper && (
              <StepCard n={4} flash={flash === 4} title="Connect trading API" state="todo">
                <p className="tb-line">Connect your Hyperliquid trading account in under a minute.</p>
                <div className="tb-row">
                  <button className="tb-cta" disabled={busy !== null} onClick={() => setConnectOpen(true)}>Connect Hyperliquid API</button>
                  <button className="tb-btn tb-btn-ghost" disabled={busy !== null} onClick={() => void usePaper()}>{busy === "paper" ? "…" : "Use Paper Trading"}</button>
                </div>
                <p className="tb-sub">You start in paper trading either way. Live trading needs your explicit confirmation, and trading can lose money.</p>
              </StepCard>
            )}
          </div>
        )}

        {connectOpen && <ConnectDialog onChanged={() => void load()} onClose={() => { setConnectOpen(false); void load(); }} />}

        {record_}

        <EarnLeaderboard minTraders={5} />

        <p className="tb-disclose">Trading is risky: you can lose money. Not financial advice. Funds stay in your own Hyperliquid account. We may earn a
          referral fee from Hyperliquid (not affiliated). Check that Hyperliquid is allowed where you live.</p>
      </div>
    </SitePage>
  );
}
