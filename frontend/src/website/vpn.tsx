import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { MemberApiError, memberApi } from "./members";
import { SitePage } from "./community";
import { reducedMotion } from "./motion";
import { pageHead } from "./seo";
import { site } from "./websiteData";
import { PLAN, STORE_LINKS, WINDOWS_BETA, configQr, saveConfig, visitorBrowser, visitorOs } from "./vpnConfig";
import { AndroidIcon, AppleIcon, ChromeIcon, EdgeIcon, LinuxIcon, WindowsIcon, WireGuardIcon } from "./vpnIcons";
import { HeroArt } from "./vpnArt";
import { Flag, NetworkMap, place, placeLine, type MapServer } from "./vpnMap";
import "./vpn.css";

/**
 * Kiwi VPN: one free 30-day trial per account, then $9.99 for 30 days renewed by hand, free for Royal Kiwis and above. The server decides all of it.
 * If Admin switches both the trial and payments off it's free for all (no `billing` from the server): sign in with a Passport,
 * add a device, and no price or payment appears anywhere. Paid: $9.99 for 30 days, any amount buys its share of time, with a Passport
 * (card or USDT) or an anonymous key (USDT only, no email or name; a lost key can't be recovered). The server decides who may use it;
 * this page only shows it. The key lives in this tab (sessionStorage) unless the visitor saves it elsewhere themselves. A new device's config (the only copy of its private key) comes back ONCE: it is shown as a QR code and a
 * .conf download, then forgotten. Lost it → delete the device and add a new one.
 */

export interface VpnServer { id: number; name: string; region: string; country_code: string; online: boolean; load: number;
  /** its resolver blocks ads & trackers */ adblock: boolean; /** the pick for a new device (online, least busy) */ recommended: boolean; /** the browser extension can use it */ browser?: boolean }
export interface VpnDevice { id: number; name: string; server_id: number; ip: string; created_at: string; last_handshake: string | null;
  rx_bytes: number; tx_bytes: number; paused_until: string | null }
/** This UTC month's data across the member's devices. cap_bytes 0 = unlimited; paused_until: over the cap, back on then. */
export interface VpnUsage { month: string; used_bytes: number; cap_bytes: number; paused_until: string | null }
export interface VpnBilling { price_usd: number; period_days: number; min_usd: number; max_usd: number; usdt: boolean; card: boolean; networks: { key: string; name: string; min_usd?: number }[] }
export type VpnPlanKind = "trial" | "royal" | "paid" | "expired" | "not_activated" | "free" | "owner" | "granted" | "blocked";
export interface VpnPlan { kind: VpnPlanKind; until: string | null; days_left: number | null }
export interface VpnState { enabled: boolean; test_mode?: boolean; eligible: boolean; reason: string | null; max_devices: number; paid_until?: string | null; anonymous?: boolean; has_card?: boolean; billing?: VpnBilling | null;
  servers: VpnServer[]; devices: VpnDevice[]; usage: VpnUsage; plan?: VpnPlan; royal?: boolean; trial?: { available: boolean; days: number } }
/** Real figures from the server. uptime stays null until real monitoring exists. */
export interface VpnStats { active_users: number; countries: number; online_servers: number; uptime: number | null;
  servers: { id: number; name: string; region: string; country_code: string; online: boolean; recommended: boolean; browser: boolean }[] }
export interface NewDevice { device: VpnDevice; config: string; filename: string }

const FALLBACK: Record<number, string> = {
  401: "Sign in to your Kiwi Passport to use Kiwi VPN.",
  403: "Your account doesn't have Kiwi VPN yet.",
  404: "That device no longer exists.",
  409: "You've reached your device limit. Delete a device to add a new one.",
  503: "That server is offline right now. Pick another server or try again in a few minutes.",
};
/** The server's message when it sent one, else a plain explanation for the status. */
export function vpnError(e: unknown): string {
  if (e instanceof MemberApiError) return e.message.startsWith("Something went wrong") ? FALLBACK[e.status] ?? e.message : e.message;
  return "Couldn't reach Price Chat. Check your connection and try again.";
}

export function bytes(n: number): string {
  if (!n || n < 0) return "0 MB";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / 1024 ** i;
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

/** "1 Nov": the day a paused VPN switches back on (UTC: always the 1st of a month). */
export function resumeDay(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

/** The server a new device should use: the API's recommendation, else the least busy online one, else the first. */
export function pickServer(servers: VpnServer[]): number {
  return servers.find((x) => x.recommended && x.online)?.id
    ?? [...servers].filter((x) => x.online).sort((a, b) => a.load - b.load || a.id - b.id)[0]?.id ?? servers[0]?.id ?? 0;
}

export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "Never connected";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "Just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} ${d === 1 ? "day" : "days"} ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export const INSTALL = [
  { os: "iPhone / iPad", href: "https://apps.apple.com/us/app/wireguard/id1441195209" },
  { os: "Android", href: "https://play.google.com/store/apps/details?id=com.wireguard.android" },
  { os: "Windows", href: "https://www.wireguard.com/install/" },
  { os: "Mac", href: "https://apps.apple.com/us/app/wireguard/id1451685025" },
];

/** Every account call goes through this: the Passport session cookie, or the anonymous key in a header. */
type Call = <T>(path: string, init?: RequestInit) => Promise<T>;
const KEY_STORE = "kiwi-vpn-key";
const callFor = (key: string | null): Call => (key
  ? (path, init = {}) => memberApi(`/members/vpn/anon${path}`, { ...init, headers: { ...(init.headers ?? {}), "X-Kiwi-Key": key } })
  : (path, init) => memberApi(`/members/vpn${path}`, init));
const CallCtx = createContext<Call>(callFor(null));
function readKey(): string | null { try { return sessionStorage.getItem(KEY_STORE); } catch { return null; } }
function saveKey(k: string | null): void { try { if (k) sessionStorage.setItem(KEY_STORE, k); else sessionStorage.removeItem(KEY_STORE); } catch { /* the key stays in memory only */ } }

type Visitor = { visitor: true; enabled: boolean; test_mode?: boolean; trial?: boolean; free?: boolean; billing: VpnBilling | null };
type Loaded = VpnState | { error: string; status: number } | Visitor;

/** The account behind both pages: a Passport session or an anonymous key, whichever this tab has. */
function useVpnAccount() {
  const [key, setKeyState] = useState<string | null>(readKey);
  const [state, setState] = useState<Loaded | null>(null);
  const setKey = useCallback((k: string | null) => { saveKey(k); setKeyState(k); }, []);
  const load = useCallback(async () => {
    try { setState(await callFor(key)<VpnState>("")); }
    catch (e) {
      const status = e instanceof MemberApiError ? e.status : 0;
      if (key && status === 401) { setKey(null); setState({ error: "That key isn't valid.", status: 0 }); return; }
      if (status === 401) {
        try { setState({ visitor: true, ...(await memberApi<Omit<Visitor, "visitor">>("/members/vpn/public")) }); return; } catch { /* the plain error below */ }
      }
      setState({ error: vpnError(e), status });
    }
  }, [key, setKey]);
  useEffect(() => { void load(); }, [load]);
  return { key, setKey, state, setState, load };
}

function TestModeNote({ paid }: { paid: boolean }) {
  return <p className="vpn-testmode" role="status"><b>Test mode</b> Kiwi VPN is closed to everyone else. You see this because you're signed in to Admin.{paid ? " Payments here are real." : ""}</p>;
}

function Gate({ error, status }: { error: string; status: number }) {
  return (
    <div className="vpn-gate">
      <p>{error}</p>
      <a className="site-button" href={status === 401 ? "/join" : "/passport"}>{status === 401 ? "Sign in" : "Back to your Passport"}</a>
    </div>
  );
}

export function VpnPage() {
  const { key, setKey, state, setState, load } = useVpnAccount();
  const [fresh, setFresh] = useState<string | null>(null);  // a key just made: shown once, before anything else
  const [stats, setStats] = useState<VpnStats | "unavailable" | null>(null);
  useEffect(() => {
    memberApi<VpnStats>("/members/vpn/stats").then(setStats).catch(() => setStats("unavailable"));
  }, []);
  const member = state && "eligible" in state ? state : null;
  const visitor = state && "visitor" in state ? state : null;
  const open = !!state && "enabled" in state && state.enabled;
  const paid = open && !!state && "billing" in state && !!state.billing;
  const free = freeForAll(state);
  const list = member && member.enabled ? member.servers : stats && stats !== "unavailable" ? stats.servers : null;
  return (
    <SitePage {...pageHead("vpn")}>
      <div className="vpn">
        <Hero state={state} servers={list ?? []} onStarted={setState} />
        {state && "test_mode" in state && state.test_mode && <TestModeNote paid={paid} />}
        {fresh && <KeyOnce k={fresh} onDone={() => { setKey(fresh); setFresh(null); }} />}
        {state && "error" in state && <Gate error={state.error} status={state.status} />}
        {!free && <Plans state={state} onStarted={setState} />}
        {!fresh && visitor?.enabled && (visitor.billing ? <Choose billing={visitor.billing} onKey={setKey} onFresh={setFresh} /> : <FreeStart onKey={setKey} trial={!!visitor.trial} />)}
        <Downloads manualOpen={open} testers={!!member?.enabled && member.servers.some((x) => x.browser)} />
        {!state ? <MySkeleton /> : member?.enabled && !fresh && (
          <CallCtx.Provider value={callFor(key)}>
            <section className="vpn-account" aria-label="Your account">
              {member.anonymous && <p className="vpn-keybar">Using your anonymous key. <button className="vpn-link" onClick={() => { setKey(null); setState(null); }}>Forget it on this browser</button></p>}
              <MyVpn s={member} onStarted={setState} />
              <PausedNote s={member} />
              <AddTime s={member} reload={load} />
            </section>
          </CallCtx.Provider>
        )}
        <Network stats={stats} list={list} />
        <RoyalKiwi member={member} />
        <Faq member={member} free={free} paid={paid} open={open} />
      </div>
    </SitePage>
  );
}

/** Account > My Devices: everything about individual devices, including the manual route. The apps won't need any of it: signing in will set a device up. */
export function VpnDevicesPage() {
  const { key, setKey, state, load } = useVpnAccount();
  const [typed, setTyped] = useState("");
  const member = state && "eligible" in state ? state : null;
  useEffect(() => { if (state && location.hash === "#manual") reveal("vpn-manual"); }, [state === null]);  // eslint-disable-line react-hooks/exhaustive-deps
  const paid = !!state && "billing" in state && !!state.billing;
  return (
    <SitePage title="My Devices · Kiwi VPN" description="Your Kiwi VPN devices and manual setup." path="/vpn/devices" noindex>
      <div className="vpn vpn-devpage">
        <nav className="vpn-crumbs" aria-label="Breadcrumb"><a href="/passport">Account</a><i aria-hidden>/</i><a href="/vpn">Kiwi VPN</a><i aria-hidden>/</i><span aria-current="page">My Devices</span></nav>
        <header className="vpn-head">
          <h1>My Devices</h1>
          {member?.enabled && <p className="vpn-head-sub">{member.devices.length} of {member.max_devices} in use. The Kiwi VPN apps will set themselves up when you sign in, so you won't need to add them here.</p>}
        </header>
        {state && "test_mode" in state && state.test_mode && <TestModeNote paid={paid} />}
        {!state ? <MySkeleton />
          : "error" in state ? <Gate error={state.error} status={state.status} />
          : !state.enabled ? <Soon />
          : "visitor" in state ? (
            <section className="vpn-card vpn-start" aria-labelledby="vpn-devs-in">
              <h2 id="vpn-devs-in">Sign in to see your devices</h2>
              <a className="site-button" href="/join">Sign in</a>
              <details className="vpn-havekey">
                <summary>I have a key</summary>
                <form className="vpn-form" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) setKey(typed.trim()); }}>
                  <label className="site-field"><span>Your key</span>
                    <input value={typed} autoComplete="off" spellCheck={false} placeholder="KIWI-XXXX-…" onChange={(e) => setTyped(e.target.value)} /></label>
                  <button className="site-button is-ghost" type="submit" disabled={!typed.trim()}>Use my key</button>
                </form>
              </details>
            </section>
          ) : (
            <CallCtx.Provider value={callFor(key)}>
              <PausedNote s={state} />
              <Unlocked s={state} reload={load} />
            </CallCtx.Provider>
          )}
        {state && !("error" in state) && state.enabled && !(member && member.enabled) && <ManualSetup form={null} />}
      </div>
    </SitePage>
  );
}

/** Opens a folded section if it is one, then brings it into view. */
function reveal(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  if (el instanceof HTMLDetailsElement) el.open = true;
  el.scrollIntoView?.({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}

/** Everyone free: Admin has both the trial and payments off. */
function freeForAll(state: Loaded | null): boolean {
  if (!state || "error" in state) return false;
  if ("visitor" in state) return state.free ?? (state.enabled && !state.billing && !state.trial);
  return planOf(state).kind === "free";
}

function SectionHead({ id, kicker, title, sub }: { id: string; kicker: string; title: string; sub?: React.ReactNode }) {
  return (
    <header className="vpn-head">
      <p className="vpn-kicker">{kicker}</p>
      <h2 id={id}>{title}</h2>
      {sub && <p className="vpn-head-sub">{sub}</p>}
    </header>
  );
}

function Soon() {
  return <div className="vpn-soon"><b>Coming soon</b><p>Kiwi VPN isn't open yet. It will appear here as soon as it is.</p></div>;
}

function MySkeleton() {
  return (
    <div className="vpn-my is-loading" role="status">
      <span className="vpn-sr">Loading your Kiwi VPN…</span>
      <i className="vpn-skel is-wide" /><i className="vpn-skel" /><i className="vpn-skel" /><i className="vpn-skel" />
    </div>
  );
}

/** Starting a trial: the server decides whether it may, and answers with the whole updated account. */
function useTrial(onStarted: (s: VpnState) => void) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const start = async () => {
    setBusy(true); setErr(null);
    try { onStarted(await memberApi<VpnState>("/members/vpn/trial", { method: "POST" })); }
    catch (e) { setErr(vpnError(e)); } finally { setBusy(false); }
  };
  return { busy, err, start };
}

const TRUST: { text: string; href?: string }[] = [
  { text: "We don't log the sites you visit", href: "/vpn/privacy" },
  { text: "We don't store your device's private key" },
];

function Hero({ state, servers, onStarted }: { state: Loaded | null; servers: MapServer[]; onStarted: (s: VpnState) => void }) {
  const { busy, err, start } = useTrial(onStarted);
  const member = state && "eligible" in state ? state : null;
  const visitor = state && "visitor" in state ? state : null;
  const open = !!state && "enabled" in state && state.enabled;
  const free = freeForAll(state);
  const trialLabel = `Start Free Trial — ${member?.trial?.days ?? 30} Days`;
  const billing = open && state && "billing" in state ? state.billing : null;
  const primary = member?.trial?.available ? <button className="site-button is-go" disabled={busy} onClick={() => void start()}>{busy ? "Starting…" : trialLabel}</button>
    : visitor?.trial && open ? <a className="site-button is-go" href="/join">{trialLabel}</a>
    : billing && member && !member.eligible ? <button className="site-button is-go" onClick={() => reveal("vpn-time")}>Add Time</button>
    : null;
  const closed = !!state && !open && !("error" in state);
  return (
    <header className="vpn-hero">
      <div className="vpn-hero-text">
        <p className="vpn-brand"><img src={site.kiwi.src} width={24} height={20} alt="" />Kiwi VPN {open && free && <span className="vpn-pill is-free">Free while we test</span>}</p>
        <h1>Your internet. <em>Your rules.</em></h1>
        <p className="vpn-hero-lead">Sites see our server's address instead of yours, and your connection is encrypted on its way there.</p>
        <div className="vpn-hero-actions">
          {primary}
          {WINDOWS_BETA.href && visitorOs(navigator.userAgent, navigator.maxTouchPoints) === "Windows"
            ? <a className="site-button vpn-windows-go" href={WINDOWS_BETA.href} download><WindowsIcon className="vpn-picon" />Download for Windows<span className="vpn-beta">Beta</span></a>
            : <a className={primary ? "site-button is-ghost" : "site-button is-go"} href="#vpn-downloads">Get Kiwi VPN</a>}
          {closed && <span className="vpn-opening" role="status"><i />Opening soon</span>}
        </div>
        {err && <p className="site-error" role="alert">{err}</p>}
        <ul className="vpn-trust">
          {TRUST.map((t) => (
            <li key={t.text}><Tick />{t.href ? <a href={t.href}>{t.text}</a> : t.text}</li>
          ))}
        </ul>
      </div>
      <HeroArt servers={servers} />
    </header>
  );
}

function Tick() {
  return <svg className="vpn-tick" viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8.500 3.200 3.200L13 4.800" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

const PLAN_NAME: Record<VpnPlanKind, string> = { trial: "Free Trial", royal: "Royal Kiwi", paid: "Paid", expired: "Expired", not_activated: "Not Activated",
  free: "Free while we test", owner: "Owner", granted: "Included", blocked: "Blocked" };

function planOf(s: VpnState): VpnPlan {
  return s.plan ?? { kind: s.eligible ? (s.billing ? "paid" : "free") : "not_activated", until: null, days_left: null };
}

function daysLeft(n: number | null): string {
  return n === 1 ? "1 day left" : `${n ?? 0} days left`;
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** A ring for time that runs out (the real days left against the real length), otherwise a mark for the kind of plan. */
function PlanMark({ plan, total }: { plan: VpnPlan; total: number }) {
  const timed = plan.days_left != null && (plan.kind === "trial" || plan.kind === "paid");
  const share = timed ? Math.max(0, Math.min(1, plan.days_left! / total)) : plan.kind === "expired" || plan.kind === "not_activated" || plan.kind === "blocked" ? 0 : 1;
  const C = 2 * Math.PI * 44;
  return (
    <span className="vpn-mark" aria-hidden>
      <svg viewBox="0 0 100 100">
        <circle className="vpn-mark-track" cx="50" cy="50" r="44" />
        <circle className="vpn-mark-fill" cx="50" cy="50" r="44" strokeDasharray={`${C * share} ${C}`} transform="rotate(-90 50 50)" />
      </svg>
      {timed ? <b>{plan.days_left}<small>{plan.days_left === 1 ? "day" : "days"}</small></b>
        : plan.kind === "royal" ? <Crown />
        : share ? <Tick />
        : <img src={site.kiwi.src} alt="" width={40} height={34} />}
    </span>
  );
}

function Crown() {
  return <svg className="vpn-crown" viewBox="0 0 32 24" aria-hidden="true"><path d="M3 20h26l2-14-8 6-7-10-7 10-8-6z" fill="currentColor" /></svg>;
}

const LIVE_MS = 10 * 60_000;  // the server reads handshakes every 5 minutes

/** The account in one compact line: plan, time, devices, data and the one thing to do next. There's no Connect button: a web page can't control a tunnel. */
function MyVpn({ s, onStarted }: { s: VpnState; onStarted: (s: VpnState) => void }) {
  const { busy, err, start } = useTrial(onStarted);
  const plan = planOf(s);
  const used = s.usage?.used_bytes ?? 0;
  const cap = s.usage?.cap_bytes ?? 0;
  const live = s.devices.filter((d) => d.last_handshake && Date.now() - new Date(d.last_handshake).getTime() < LIVE_MS).length;
  const detail = plan.kind === "royal" ? "No expiry while you keep your rank."
    : plan.kind === "free" ? "Free for everyone during testing."
    : plan.kind === "owner" ? "Made in Admin."
    : plan.until ? `${daysLeft(plan.days_left)} · until ${day(plan.until)}`
    : s.reason ?? null;
  const hint = s.trial?.available ? "One free trial per account."
    : !s.eligible ? (s.billing ? null : plan.kind === "blocked" ? null : "Purchasing isn't open yet.")
    : null;
  return (
    <section id="my-vpn" className={`vpn-my is-${plan.kind}`} aria-labelledby="vpn-my-title">
      <div className="vpn-my-plan">
        <PlanMark plan={plan} total={s.trial?.days ?? s.billing?.period_days ?? 30} />
        <div>
          <h2 id="vpn-my-title">My Kiwi VPN</h2>
          <p className="vpn-big">{PLAN_NAME[plan.kind]}</p>
          {detail && <p className="vpn-my-detail">{detail}</p>}
        </div>
      </div>
      <dl className="vpn-facts">
        <div>
          <dt>Devices</dt>
          <dd>{s.devices.length} <small>/ {s.max_devices}</small></dd>
          <small>{live ? `${live} connected in last 10 min` : s.devices.length ? "None active recently" : "None added yet"}</small>
        </div>
        <div>
          <dt>Data this month</dt>
          <dd>{bytes(used)}</dd>
          {s.usage && cap > 0 ? <UsageMeter u={s.usage} /> : <small>This month · no limit</small>}
        </div>
      </dl>
      <div className="vpn-my-next">
        {s.trial?.available ? <button className="site-button is-go" disabled={busy} onClick={() => void start()}>{busy ? "Starting…" : `Start Free Trial — ${s.trial.days} Days`}</button>
          : !s.eligible && s.billing && !["royal", "owner", "blocked"].includes(plan.kind) ? <button className="site-button is-go" onClick={() => reveal("vpn-time")}>Add Time</button>
          : null}
        {s.eligible && s.billing && !["royal", "owner"].includes(plan.kind) && <button className="vpn-textlink" onClick={() => reveal("vpn-time")}>Add time</button>}
        <a className="vpn-textlink" href="/vpn/devices">My Devices</a>
        {hint && <small>{hint}</small>}
        {err && <p className="site-error" role="alert">{err}</p>}
      </div>
    </section>
  );
}

/** The official WireGuard app for each system: manual setup only. */
const DEVICE_ROWS = [
  { name: "Windows", href: "https://www.wireguard.com/install/", Icon: WindowsIcon },
  { name: "macOS", href: "https://apps.apple.com/us/app/wireguard/id1451685025", Icon: AppleIcon },
  { name: "Linux", href: "https://www.wireguard.com/install/", Icon: LinuxIcon },
  { name: "Android", href: "https://play.google.com/store/apps/details?id=com.wireguard.android", Icon: AndroidIcon },
  { name: "iOS", href: "https://apps.apple.com/us/app/wireguard/id1441195209", Icon: AppleIcon },
];

function AppCard({ icons, name, text, status, on = false, mine, children }: { icons: React.ReactNode; name: string; text: string; status: string; on?: boolean; mine?: string | null; children?: React.ReactNode }) {
  return (
    <li className={`vpn-app${mine ? " is-mine" : ""}`}>
      <span className="vpn-app-icons">{icons}</span>
      <h3>{name}{mine && <small className="vpn-yours">{mine}</small>}</h3>
      <p>{text}</p>
      <div className="vpn-app-foot">
        <span className={`vpn-status${on ? " is-on" : ""}`}>{status}</span>
        {children}
      </div>
    </li>
  );
}

/** Available means a real, working store link. Anything else says so plainly and offers only the manual route. Nothing here is a download until it exists. */
export function Downloads({ agent = navigator.userAgent, touch = navigator.maxTouchPoints, testers = false, manualOpen = true }: { agent?: string; touch?: number; /** the tester ZIP: a signed-in member, with a server that takes it */ testers?: boolean; /** manual setup leads somewhere only once access is open */ manualOpen?: boolean }) {
  const browser = visitorBrowser(agent);
  const os = visitorOs(agent, touch);
  const [zip, setZip] = useState(false);
  const stores = [
    { key: "chrome", href: STORE_LINKS.chrome, action: "Add to Chrome" },
    { key: "edge", href: STORE_LINKS.edge, action: "Get for Edge" },
  ].filter((x) => x.href);
  const manual = (name: string) => manualOpen && <a className="vpn-textlink" aria-label={`Manual setup for ${name}`} href="/vpn/devices#manual">Manual setup</a>;
  return (
    <section id="vpn-downloads" className="vpn-band vpn-downloads site-reveal" aria-labelledby="vpn-download">
      <SectionHead id="vpn-download" kicker="Apps" title="Get Kiwi VPN" />
      <ul className="vpn-apps">
        {WINDOWS_BETA.href ? (
          <AppCard icons={<WindowsIcon className="vpn-app-icon" />} name="Windows desktop" text="Covers every app on your PC. Setup is temporary for now: the beta needs a config file from My Devices." status="Beta" on mine={os === "Windows" ? "Your device" : null}>
            <a className="site-button is-small vpn-windows-go" href={WINDOWS_BETA.href} download><WindowsIcon className="vpn-picon" />Download for Windows</a>
            {manual("Windows")}
          </AppCard>
        ) : (
          <AppCard icons={<WindowsIcon className="vpn-app-icon" />} name="Windows desktop" text="Covers every app on your PC." status="In development" mine={os === "Windows" ? "Your device" : null}>{manual("Windows")}</AppCard>
        )}
        <AppCard icons={<><ChromeIcon className="vpn-app-icon" /><EdgeIcon className="vpn-app-icon" /></>} name="Browser extension" text="For Chrome and Edge. Covers that browser only." status={stores.length ? "Available" : "Coming soon"} on={stores.length > 0} mine={browser === "chrome" || browser === "edge" ? "Your browser" : null}>
          {stores.map((x) => <a key={x.key} className="site-button is-small" href={x.href} target="_blank" rel="noopener noreferrer">{x.action}</a>)}
        </AppCard>
        <AppCard icons={<AndroidIcon className="vpn-app-icon" />} name="Android" text="Covers every app on your phone." status="Coming soon" mine={os === "Android" ? "Your device" : null}>{manual("Android")}</AppCard>
        <AppCard icons={<AppleIcon className="vpn-app-icon" />} name="iOS / macOS" text="For iPhone, iPad and Mac. Covers every app on your device." status="Coming soon" mine={os === "iOS" || os === "macOS" ? "Your device" : null}>{manual(os === "macOS" ? "macOS" : "iOS")}</AppCard>
      </ul>
      {browser === "phone" && <p className="vpn-note">Browser extensions need a computer.</p>}
      {testers && (!STORE_LINKS.chrome || !STORE_LINKS.edge) && browser !== "phone" && (
        <details className="vpn-fold is-small">
          <summary>Manual install, for testers (Chrome and Edge)</summary>
          <div className="vpn-fold-body">
            <p className="site-muted">This isn't the store version. It needs Developer mode, and it won't update by itself.</p>
            <a className="site-button is-ghost is-small" href="/downloads/kiwi-vpn-extension.zip" download onClick={() => setZip(true)}>Download the ZIP</a>
            {zip && <p className="vpn-done" role="status"><Tick />Your download should start now. Then follow these steps.</p>}
            <ol className="vpn-steps">
              <li>Unzip it somewhere you'll keep it.</li>
              <li>Open <b>chrome://extensions</b> or <b>edge://extensions</b> and turn on <b>Developer mode</b>.</li>
              <li>Click <b>Load unpacked</b> and pick the <b>kiwi-vpn-extension</b> folder.</li>
              <li>Click the Kiwi VPN icon and press the switch.</li>
            </ol>
          </div>
        </details>
      )}
      {manualOpen && <p className="vpn-manual-line"><WireGuardIcon className="vpn-fold-icon" />On another system, or need it today? <a className="vpn-textlink" href="/vpn/devices#manual">Set it up manually</a> with the free WireGuard app.</p>}
    </section>
  );
}

/** The advanced route: the WireGuard app and a config file. `form` is the add-a-device form, for an account that can add one. */
function ManualSetup({ form, startOpen = false }: { form: React.ReactNode; startOpen?: boolean }) {
  return (
    <details id="vpn-manual" className="vpn-fold" open={startOpen}>
      <summary><WireGuardIcon className="vpn-fold-icon" /><span>Advanced: manual setup<small>Connect any device with the free WireGuard app and a config file.</small></span></summary>
      <div className="vpn-fold-body">
        <ol className="vpn-manual-steps">
          <li>
            <b>Install the WireGuard app</b>
            <span>It's free and official, made by the WireGuard project.</span>
            <div className="vpn-wg">
              {DEVICE_ROWS.map(({ name, href, Icon }) => (
                <a key={name} href={href} target="_blank" rel="noopener noreferrer" aria-label={`WireGuard Setup for ${name}`}><Icon className="vpn-picon" />{name}</a>
              ))}
            </div>
          </li>
          <li>
            <b>Add a device</b>
            <span>Name it and pick a location. You get a QR code and a file, shown only once.</span>
            {form}
          </li>
          <li>
            <b>Import it and switch on</b>
            <span>Scan the QR code on a phone, or import the file on a computer.</span>
          </li>
        </ol>
      </div>
    </details>
  );
}

/** Real configured servers only. A signed-in member's list carries load, the public one doesn't. No latency: nothing measures it. "Recommended" is the server's own pick: online with the lowest load. */
function Network({ stats, list }: { stats: VpnStats | "unavailable" | null; list: (VpnServer | VpnStats["servers"][number])[] | null }) {
  const [focus, setFocus] = useState<number | null>(null);
  const real = stats && stats !== "unavailable" ? stats : null;
  const online = list?.filter((x) => x.online) ?? [];
  const countries = new Set(online.map((x) => (x.country_code || "").toUpperCase()).filter(Boolean)).size;
  const cityCount = (x: MapServer) => list?.filter((o) => place(o).city === place(x).city && (o.country_code || "").toUpperCase() === (x.country_code || "").toUpperCase()).length ?? 0;
  return (
    <section id="vpn-network" className="vpn-section vpn-network site-reveal" aria-labelledby="vpn-servers">
      <SectionHead id="vpn-servers" kicker="Where you can connect" title="Global Network" sub={list && list.length > 0
        ? <>{countries} {countries === 1 ? "country" : "countries"} · {online.length} {online.length === 1 ? "server" : "servers"} online{real && real.active_users > 0 ? ` · ${real.active_users} active in the last 10 minutes` : ""}</>
        : undefined} />
      <div className="vpn-net">
        {list && list.length > 0 ? <NetworkMap servers={list} focus={focus} /> : <div className="vpn-map"><span className="vpn-map-wait" /></div>}
        <div className="vpn-net-list">
          {!list ? (stats === "unavailable" ? <p className="site-muted">Server status is unavailable right now.</p> : <div role="status"><span className="vpn-sr">Loading servers…</span><i className="vpn-skel" /><i className="vpn-skel" /></div>)
            : list.length === 0 ? <p className="site-muted">No servers are available yet.</p> : (
            <ul className="vpn-servers">
              {list.map((x) => {
                const p = place(x);
                const title = !p.city ? p.country : cityCount(x) > 1 ? x.name : p.city;
                return (
                  <li key={x.id} className={`${x.online ? "" : "is-off"}${focus === x.id ? " is-focus" : ""}`}
                      onMouseEnter={() => setFocus(x.id)} onMouseLeave={() => setFocus(null)} onClick={() => setFocus(x.id)}>
                    <Flag cc={x.country_code} />
                    <span className="vpn-sname">
                      <b>{title}{x.recommended && <span className="vpn-tag" title="Online with the lowest load right now">Recommended</span>}</b>
                      <small>{p.city ? p.country : ""}{p.city && " · "}{x.browser ? "Devices and browsers" : "Devices"}</small>
                      {"adblock" in x && x.adblock && <small className="vpn-adblock">Blocks ads & trackers</small>}
                    </span>
                    <span className={`vpn-online${x.online ? " is-on" : ""}`}><i className={`vpn-dot${x.online ? " is-on" : ""}`} aria-label={x.online ? "Online" : "Offline"} title={x.online ? "Online" : "Offline"} />{x.online ? "Online" : "Offline"}</span>
                    {"load" in x && <LoadBar load={x.load} />}
                  </li>
                );
              })}
            </ul>
          )}
          {list && list.length > 0 && (
            <p className="vpn-net-note">{list.some((x) => "load" in x) ? "The bar shows how full each server is. " : ""}Recommended means online with the lowest load right now. We don't show speed or ping because we don't measure them.</p>
          )}
        </div>
      </div>
    </section>
  );
}

/** The one place prices appear. Each button is the real next step for this account, or says plainly that there isn't one. */
function Plans({ state, onStarted }: { state: Loaded | null; onStarted: (s: VpnState) => void }) {
  const { busy, err, start } = useTrial(onStarted);
  const member = state && "eligible" in state ? state : null;
  const visitor = state && "visitor" in state ? state : null;
  const open = !!state && "enabled" in state && state.enabled;
  const billing = open && state && "billing" in state ? state.billing ?? null : null;
  const canBuy = !!billing && (billing.usdt || billing.card);
  const plan = member ? planOf(member) : null;
  const days = member?.trial?.days ?? PLAN.trial_days;
  const price = billing?.price_usd ?? PLAN.price_usd;
  const period = billing?.period_days ?? PLAN.period_days;
  const trial = member?.trial?.available ? <button className="site-button is-ghost" disabled={busy} onClick={() => void start()}>{busy ? "Starting…" : `Start Free Trial — ${days} Days`}</button>
    : visitor?.trial && open ? <a className="site-button is-ghost" href="/join">Sign in to start</a>
    : plan?.kind === "trial" ? <span className="vpn-plan-note is-on"><Tick />Active · {daysLeft(plan.days_left)}</span>
    : member ? <span className="vpn-plan-note">Not available on this account</span>
    : <span className="vpn-plan-note">Opens with Kiwi VPN</span>;
  const buy = plan?.kind === "royal" || plan?.kind === "owner" ? <span className="vpn-plan-note">Not needed on your plan</span>
    : !canBuy ? <span className="vpn-plan-note">Purchasing isn't open yet</span>
    : member ? <button className="site-button is-go" onClick={() => reveal("vpn-time")}>Add Time</button>
    : <button className="site-button is-go" onClick={() => reveal("vpn-get")}>Buy Time</button>;
  return (
    <section id="vpn-plans" className="vpn-section vpn-plans site-reveal" aria-labelledby="vpn-plans-title">
      <SectionHead id="vpn-plans-title" kicker="Pricing" title="Simple pricing." />
      <ul className="vpn-plan-list">
        <li>
          <h3>Free Trial</h3>
          <p className="vpn-price">{days}<small>days</small></p>
          <p>Free, with no payment details. One trial per account.</p>
          <div>{trial}</div>
        </li>
        <li className="is-main">
          <h3>Kiwi VPN Subscription</h3>
          <p className="vpn-price">${price}<small>per {period} days</small></p>
          <p>You renew by hand. No automatic billing.{billing?.usdt && !billing.card ? " Paid in USDT." : ""}</p>
          <div>{buy}</div>
        </li>
        <li className="is-gold">
          <h3>Royal Kiwi</h3>
          <p className="vpn-price">Free<small>with your rank</small></p>
          <p>While you hold Royal Kiwi rank or higher.</p>
          <div>{member?.royal ? <span className="vpn-plan-note is-gold"><Tick />Your plan</span> : <a className="vpn-royal-link" href="#royal">How Royal works</a>}</div>
        </li>
      </ul>
      <p className="vpn-after"><b>When your trial ends</b> nothing is charged. Your devices switch off and are kept for {PLAN.keep_days} days, and adding time switches them back on.</p>
      {err && <p className="site-error" role="alert">{err}</p>}
    </section>
  );
}

function RoyalKiwi({ member }: { member: VpnState | null }) {
  return (
    <section id="royal" className="vpn-royal site-reveal" aria-labelledby="vpn-royal">
      <div className="vpn-royal-art" aria-hidden>
        <svg viewBox="0 0 200 200" focusable="false">
          <circle cx="100" cy="100" r="96" />
          <circle cx="100" cy="100" r="84" strokeDasharray="1 7" strokeLinecap="round" />
        </svg>
        <Crown />
        <i className="vpn-royal-kiwi" />
      </div>
      <div className="vpn-royal-text">
        <p className="vpn-kicker">A Price Chat rank benefit</p>
        <h2 id="vpn-royal">Royal Kiwi</h2>
        <p className="vpn-royal-lead">Reach one of Price Chat's highest ranks and your Kiwi VPN is on us, with no fixed end date for as long as you keep the rank.</p>
        <p className="vpn-royal-ranks">Royal Kiwi <i /> Golden Kiwi <i /> Kiwi Legend <i /> Mythic Kiwi</p>
        <div className="vpn-royal-foot">
          {member?.royal ? <p className="vpn-royal-on" role="status"><Tick />Royal Kiwi Access Active</p>
            : member?.anonymous ? <p className="vpn-royal-you">Ranks belong to a Kiwi Passport, not an anonymous key.</p>
            : member ? <p className="vpn-royal-you">Your rank doesn't include this yet.</p>
            : <p className="vpn-royal-you">Sign in to check your rank.</p>}
          <a className="vpn-royal-link" href="/passport">View My Kiwi Passport</a>
        </div>
      </div>
    </section>
  );
}

function Faq({ member, free, paid, open }: { member: VpnState | null; free: boolean; paid: boolean; open: boolean }) {
  const items: [string, React.ReactNode][] = [
    ["What's the difference between the device and browser versions?", "A browser extension sends only that browser's traffic through Kiwi VPN. Other apps keep your normal connection. A device connection covers every app on the device."],
    ["Do you keep logs?", <>We don't log the websites you visit, your DNS lookups or the content of your traffic. We do keep your account, your devices' names and keys, and how much data each one used this month and when it was last active. Our <a href="/vpn/privacy">privacy page</a> lists all of it, including ordinary server logs.</>],
    ["Will a VPN make me anonymous?", "No VPN can promise that. Kiwi VPN hides your address from the sites you visit, but they can still recognise you through accounts and cookies, and your traffic does pass through our server."],
    ["Is there a Kiwi VPN app?", `Not yet. A native Windows app is our first, and it's in development.${open ? " Until then, manual setup connects any device with the free WireGuard app." : ""} Browser extensions appear here once each store approves them.`],
    ["Do I have to set up each device myself?", `With the apps, no: signing in will set the device up.${open ? " Manual setup is there for anything else, under My Devices." : ""}`],
    ...(member ? [["How many devices can I use?", `Your account can have ${member.max_devices} ${member.max_devices === 1 ? "device" : "devices"} at a time. Delete one to add another.`] as [string, React.ReactNode]] : []),
    ...(!free ? [["What happens when my time runs out?", `Your devices are switched off and kept for 30 days.${paid ? " Add time and they switch back on within 5 minutes." : ""} Nothing renews or charges by itself.`] as [string, React.ReactNode]] : []),
  ];
  return (
    <section id="vpn-faq" className="vpn-section vpn-faq site-reveal" aria-labelledby="vpn-faq-title">
      <SectionHead id="vpn-faq-title" kicker="Questions" title="Good to know." />
      <div className="vpn-faq-grid">
        <div className="vpn-faq-list">
          {items.map(([q, a]) => (
            <details key={q} className="vpn-q">
              <summary>{q}</summary>
              <p>{a}</p>
            </details>
          ))}
        </div>
        <aside className="vpn-help">
          <h3>Need a hand?</h3>
          <p>The team answers fastest on Telegram or X.</p>
          <a href="/website/contact">Contact Price Chat</a>
          <a className="vpn-privacy" href="/vpn/privacy">How Kiwi VPN handles your data</a>
          <a href="/website/terms">Terms</a>
        </aside>
      </div>
    </section>
  );
}

/** The store-review account's sign-in: a plain email and password. Everyone else signs in at /join. */
export function VpnReviewerPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await memberApi("/members/auth/reviewer", { method: "POST", body: JSON.stringify({ email: email.trim(), password }) });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't sign in. Please try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <SitePage title="Kiwi VPN store review" description="Sign-in for browser store reviewers." path="/vpn/reviewer" noindex>
      <section className="vpn">
        <section className="vpn-card vpn-start" aria-labelledby="vpn-reviewer">
          <h2 id="vpn-reviewer">Kiwi VPN store review</h2>
          {done ? (
            <p className="site-muted" role="status">You're signed in. Click the Kiwi VPN icon in your browser's toolbar and press the switch to connect.</p>
          ) : (
            <form className="vpn-form" onSubmit={(e) => void submit(e)}>
              <p className="site-muted">For browser store reviewers. Everyone else, <a className="vpn-privacy" href="/join">sign in here</a>.</p>
              <label className="site-field"><span>Email</span>
                <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
              <label className="site-field"><span>Password</span>
                <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
              {error && <p className="site-error" role="alert">{error}</p>}
              <button className="site-button" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
            </form>
          )}
        </section>
      </section>
    </SitePage>
  );
}

/** Free: one way in, nothing about money. A key from before still works. */
function FreeStart({ onKey, trial = false }: { onKey: (k: string) => void; trial?: boolean }) {
  const [typed, setTyped] = useState("");
  return (
    <section id="vpn-get" className="vpn-card vpn-start" aria-labelledby="vpn-start">
      <h2 id="vpn-start">Get connected in three steps</h2>
      <ol className="vpn-path">
        <li><b>Sign in</b><span>With your free Kiwi Passport.</span></li>
        <li><b>{trial ? "Start your free trial" : "Add a device"}</b><span>{trial ? "30 days, then add a device." : "Name it and pick a server."}</span></li>
        <li><b>Scan and switch on</b><span>In the free WireGuard app.</span></li>
      </ol>
      <a className="site-button" href="/join">Sign in to start</a>
      <details className="vpn-havekey">
        <summary>I have a key</summary>
        <form className="vpn-form" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) onKey(typed.trim()); }}>
          <label className="site-field"><span>Your key</span>
            <input value={typed} autoComplete="off" spellCheck={false} placeholder="KIWI-XXXX-…" onChange={(e) => setTyped(e.target.value)} /></label>
          <button className="site-button is-ghost" type="submit" disabled={!typed.trim()}>Use my key</button>
        </form>
      </details>
    </section>
  );
}

/** A signed-out visitor picks: a Passport account, or an anonymous key. */
function Choose({ billing, onKey, onFresh }: { billing: VpnBilling; onKey: (k: string) => void; onFresh: (k: string) => void }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function make() {
    setBusy(true); setErr(null);
    try { onFresh((await memberApi<{ key: string }>("/members/vpn/anon", { method: "POST" })).key); }
    catch (e) { setErr(vpnError(e)); } finally { setBusy(false); }
  }
  return (
    <div id="vpn-get" className="vpn-grid">
      <section className="vpn-card" aria-labelledby="vpn-passport">
        <h2 id="vpn-passport">With a Passport</h2>
        <p className="site-muted">Sign in with your email. Pay by {billing.card ? "card or USDT" : "USDT"}, and recover your account if you lose a device.</p>
        <a className="site-button" href="/join">Sign in</a>
      </section>
      <section className="vpn-card" aria-labelledby="vpn-anon">
        <h2 id="vpn-anon">Anonymous, with USDT</h2>
        <p className="site-muted">No email, no username, no password. You get a key, and that key is your only login.
          <b> Lose it and your time is gone: nobody can recover it.</b> Keeping your own crypto anonymous is up to you.</p>
        {!billing.usdt && <p className="site-muted">USDT payments open soon.</p>}
        <button className="site-button" disabled={busy || !billing.usdt} onClick={() => void make()}>{busy ? "Creating…" : "Get a key"}</button>
        {err && <p className="site-error" role="alert">{err}</p>}
        <form className="vpn-form" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) onKey(typed.trim()); }}>
          <label className="site-field"><span>Already have a key?</span>
            <input value={typed} autoComplete="off" spellCheck={false} placeholder="KIWI-XXXX-…" onChange={(e) => setTyped(e.target.value)} /></label>
          <button className="site-button is-ghost" type="submit" disabled={!typed.trim()}>Use my key</button>
        </form>
      </section>
    </div>
  );
}

/** The new key, once. Continuing needs a tick: there is no recovery, so this is the one moment that matters. */
function KeyOnce({ k, onDone }: { k: string; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  return (
    <section className="vpn-card vpn-keyonce" aria-labelledby="vpn-key-title">
      <h2 id="vpn-key-title">Save your key</h2>
      <p>This is your only login, and it's shown <b>once</b>. We keep only a scrambled form of it, so we can't show it again or recover it.</p>
      <code className="vpn-key">{k}</code>
      <button className="site-button is-ghost" onClick={() => { void navigator.clipboard?.writeText(k); setCopied(true); }}>{copied ? "Copied ✓" : "Copy key"}</button>
      <label className="vpn-check"><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> I saved my key somewhere safe. If I lose it, my time is gone.</label>
      <button className="site-button" disabled={!saved} onClick={onDone}>Continue</button>
    </section>
  );
}

type Invoice = { network_name: string; address: string; amount: number; amount_usd: number; expires_at: string | null; days: number };

/** Time left, and buying more. Any amount buys its share of $9.99 for 30 days. */
function AddTime({ s, reload }: { s: VpnState; reload: () => Promise<void> }) {
  const call = useContext(CallCtx);
  const b = s.billing;
  const [amount, setAmount] = useState("9.99");
  const [network, setNetwork] = useState("solana");  // the lowest fees; each network's minimum comes from the server
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [startOpen] = useState(!s.eligible);
  useEffect(() => {  // the time appears by itself once the network confirms the payment
    if (!invoice) return;
    const id = window.setInterval(() => void reload(), 20000);
    return () => window.clearInterval(id);
  }, [invoice, reload]);
  if (!b || s.plan?.kind === "royal" || s.plan?.kind === "owner") return null;  // nothing to buy
  const usd = Number(amount);
  const days = usd > 0 ? (usd / b.price_usd) * b.period_days : 0;
  const floor = Math.max(b.min_usd, b.networks.find((n) => n.key === network)?.min_usd ?? 0);
  const okAmount = usd >= floor && usd <= b.max_usd;
  const until = s.paid_until ? new Date(s.paid_until) : null;
  const active = until !== null && until.getTime() > Date.now();
  async function usdt(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { setInvoice(await call<Invoice>("/usdt", { method: "POST", body: JSON.stringify({ amount_usd: usd, network }) })); }
    catch (e2) { setErr(vpnError(e2)); } finally { setBusy(false); }
  }
  async function go(path: string) {
    setBusy(true); setErr(null);
    try { window.location.assign((await call<{ url: string }>(path, { method: "POST" })).url); }
    catch (e2) { setErr(vpnError(e2)); setBusy(false); }
  }
  return (
    <details id="vpn-time" className="vpn-fold" open={startOpen}>
      <summary><span id="vpn-time-title">Your time<small>{active ? `Paid until ${day(s.paid_until!)}` : "Add time to your plan"}</small></span></summary>
      <div className="vpn-fold-body vpn-time">
      <p>{active ? <>Paid until <b>{until!.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</b>.</>
        : until ? "Your time has run out. Your devices are off and kept for 30 days: add time and they switch back on within 5 minutes." : "No paid time yet."}</p>
      {!b.usdt && !b.card && <p className="site-muted">Payments open soon.</p>}
      {s.has_card && !s.anonymous && <button className="site-button is-ghost" disabled={busy} onClick={() => void go("/card/portal")}>Manage card subscription</button>}
      {b.usdt && (invoice ? (
        <div className="vpn-invoice" role="status">
          <p>Send exactly <b>{invoice.amount} USDT</b> on <b>{invoice.network_name}</b> to:</p>
          <code className="vpn-key">{invoice.address}</code>
          <button className="site-button is-ghost" onClick={() => void navigator.clipboard?.writeText(invoice.address)}>Copy address</button>
          <p className="site-muted">Only {invoice.network_name}: other coins or networks can be lost. You'll get time for what actually arrives, about {invoice.days} days for this amount, a few minutes after the network confirms.</p>
          <button className="vpn-link" onClick={() => setInvoice(null)}>Start over</button>
        </div>
      ) : (
        <form className="vpn-form" onSubmit={usdt}>
          <label className="site-field"><span>Amount (USD)</span>
            <input type="number" inputMode="decimal" min={b.min_usd} max={b.max_usd} step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
            <small>{okAmount ? `Buys about ${days.toFixed(1)} days` : `$${floor} to $${b.max_usd} on this network`}</small></label>
          <label className="site-field"><span>Pay with</span>
            <select className="site-input" value={network} onChange={(e) => setNetwork(e.target.value)}>
              {b.networks.map((n) => <option key={n.key} value={n.key}>{n.name}{n.min_usd && n.min_usd > b.min_usd ? ` (min $${n.min_usd})` : ""}</option>)}
            </select></label>
          <button className="site-button" type="submit" disabled={busy || !okAmount}>{busy ? "Creating…" : "Pay with USDT"}</button>
        </form>
      ))}
      {err && <p className="site-error" role="alert">{err}</p>}
      <p className="site-muted vpn-fine">$9.99 buys 30 days and any amount buys its share. You renew by hand: nothing is charged automatically. Network fees are yours. USDT payments can't be refunded.</p>
      </div>
    </details>
  );
}

function PausedNote({ s }: { s: VpnState }) {
  const paused = s.usage?.paused_until;
  if (!paused) return null;
  return (
    <p className="vpn-paused" role="status"><b>Paused for the rest of the month</b> You've used this month's {bytes(s.usage.cap_bytes)} of
      Kiwi VPN data. Your devices are kept and switch back on by themselves on {resumeDay(paused)}.</p>
  );
}

function Unlocked({ s, reload }: { s: VpnState; reload: () => Promise<void> }) {
  const [created, setCreated] = useState<NewDevice | null>(null);
  const full = s.devices.length >= s.max_devices;
  const serverName = (id: number) => s.servers.find((x) => x.id === id);
  const paused = s.usage?.paused_until ?? null;
  return (
    <>
      {created && <ConfigOnce created={created} server={serverName(created.device.server_id)} onDone={() => setCreated(null)} />}
      <section className="vpn-card vpn-list" aria-labelledby="vpn-devices">
        <h2 id="vpn-devices">Your devices</h2>
        {s.devices.length === 0 ? <p className="site-muted">No devices yet.</p> : (
          <ul className="vpn-devices">
            {s.devices.map((d) => <DeviceRow key={d.id} d={d} server={serverName(d.server_id)} reload={reload} />)}
          </ul>
        )}
      </section>
      <ManualSetup form={!s.eligible ? <p className="site-muted">{s.reason ?? "Your account can't add a device right now."} <a className="vpn-textlink" href="/vpn#vpn-plans">See pricing</a></p>
        : created ? null : <AddDevice servers={s.servers} full={full} max={s.max_devices} count={s.devices.length} paused={!!paused}
                                      onCreated={(n) => { setCreated(n); void reload(); }} />} />
    </>
  );
}

function LoadBar({ load }: { load: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, load || 0)) * 100);
  return (
    <span className={`vpn-load${pct >= 85 ? " is-busy" : pct >= 60 ? " is-mid" : ""}`} role="meter" aria-label="Server load"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <i style={{ width: `${pct}%` }} />
    </span>
  );
}

/** This month's data: "1.5 GB of 50 GB this month" with a bar when there's a cap, else the total and "no limit". */
function UsageMeter({ u }: { u: VpnUsage }) {
  const pct = u.cap_bytes > 0 ? Math.round(Math.min(1, u.used_bytes / u.cap_bytes) * 100) : 0;
  return (
    <div className="vpn-usage">
      <span><b>{bytes(u.used_bytes)}</b> {u.cap_bytes > 0 ? <>of {bytes(u.cap_bytes)} this month</> : <>this month · no limit</>}</span>
      {u.cap_bytes > 0 && (
        <span className={`vpn-load${pct >= 90 ? " is-busy" : pct >= 70 ? " is-mid" : ""}`} role="meter" aria-label="Data used this month"
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <i style={{ width: `${pct}%` }} />
        </span>
      )}
    </div>
  );
}

function AddDevice({ servers, full, max, count, paused, onCreated }: {
  servers: VpnServer[]; full: boolean; max: number; count: number; paused: boolean; onCreated: (n: NewDevice) => void;
}) {
  const pick = () => pickServer(servers);
  const [name, setName] = useState("");
  const [server, setServer] = useState<number>(pick);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!servers.some((x) => x.id === server)) setServer(pick()); }, [servers]);  // eslint-disable-line react-hooks/exhaustive-deps
  const clean = name.trim();
  const call = useContext(CallCtx);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!clean || !server || busy) return;
    setBusy(true); setErr(null);
    try {
      const n = await call<NewDevice>("/devices", { method: "POST", body: JSON.stringify({ name: clean, server_id: server }) });
      setName("");
      onCreated(n);
    } catch (e2) {
      setErr(vpnError(e2));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="vpn-add" aria-label="Add a device">
      {paused ? <p className="site-muted">You can add devices again when your data resets on the 1st.</p>
        : full ? <p className="site-muted">You're using all {max} of your devices ({count} / {max}). Delete one to add another.</p> : servers.length === 0 ? (
        <p className="site-muted">No servers are available yet. Check back soon.</p>
      ) : (
        <form className="vpn-form" onSubmit={submit}>
          <label className="site-field">
            <span>Device name</span>
            <input value={name} maxLength={32} placeholder="e.g. My iPhone" onChange={(e) => setName(e.target.value)} required />
          </label>
          <label className="site-field">
            <span>Location</span>
            <select className="site-input" value={server} onChange={(e) => setServer(Number(e.target.value))}>
              {servers.map((x) => (
                <option key={x.id} value={x.id} disabled={!x.online}>
                  {placeLine(x)}{servers.filter((o) => placeLine(o) === placeLine(x)).length > 1 ? ` · ${x.name}` : ""}{x.online ? "" : " (offline)"}{x.recommended ? " · Recommended" : ""}
                </option>
              ))}
            </select>
          </label>
          <button className="site-button" type="submit" disabled={busy || !clean || !server}>{busy ? "Creating…" : "Add device"}</button>
        </form>
      )}
      {err && <p className="site-error" role="alert">{err}</p>}
    </section>
  );
}

/** The new device's config, shown once: QR code, .conf download, copy, and how to set it up. */
function ConfigOnce({ created, server, onDone }: { created: NewDevice; server?: VpnServer; onDone: () => void }) {
  const [qr, setQr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let alive = true;
    configQr(created.config).then((url) => alive && setQr(url)).catch(() => alive && setQr(""));
    return () => { alive = false; };
  }, [created.config]);
  const download = () => saveConfig(created.config, created.filename);
  async function copy() {
    try { await navigator.clipboard.writeText(created.config); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    catch { setCopied(false); }
  }
  function done() {
    if (window.confirm("Did you save the config? It can't be shown again.")) onDone();
  }
  return (
    <section className="vpn-card vpn-once" aria-labelledby="vpn-once">
      <h2 id="vpn-once">{created.device.name} is ready{server ? ` · ${placeLine(server)}` : ""}</h2>
      <p className="vpn-warn" role="alert"><b>This is shown once — save it now.</b> We don't keep a copy of your private key. If you lose it,
        delete this device and add a new one.</p>
      <div className="vpn-qr">
        {qr ? <img src={qr} alt={`QR code for ${created.device.name}`} width={260} height={260} />
          : qr === "" ? <p className="site-muted">Couldn't draw the QR code: download the file instead.</p>
          : <span className="vpn-qr-wait" aria-label="Drawing the QR code" />}
      </div>
      <div className="vpn-actions">
        <button className="site-button" type="button" onClick={download}>Download .conf</button>
        <button className="site-textbutton" type="button" onClick={() => void copy()}>{copied ? "Copied ✓" : "Copy config"}</button>
      </div>
      <ol className="vpn-steps">
        <li><b>Install WireGuard</b> (free, official):{" "}
          {INSTALL.map((i, n) => <span key={i.os}>{n > 0 && " · "}<a href={i.href} target="_blank" rel="noopener noreferrer">{i.os}</a></span>)}
          <small> All apps: <a href="https://www.wireguard.com/install/" target="_blank" rel="noopener noreferrer">wireguard.com/install</a></small></li>
        <li><b>Add the tunnel:</b> on a phone, tap + → “Scan from QR code”. On a computer, “Import tunnel(s) from file” and pick the .conf.</li>
        <li><b>Switch it on.</b> That's it: you're browsing through Kiwi VPN.</li>
      </ol>
      <button className="site-textbutton" type="button" onClick={done}>I saved it · Done</button>
    </section>
  );
}

function DeviceRow({ d, server, reload }: { d: VpnDevice; server?: VpnServer; reload: () => Promise<void> }) {
  const call = useContext(CallCtx);
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function remove() {
    setBusy(true); setErr(null);
    try { await call<void>(`/devices/${d.id}`, { method: "DELETE" }); await reload(); }
    catch (e) { setErr(vpnError(e)); setBusy(false); setAsk(false); }
  }
  return (
    <li className="vpn-device">
      <Flag cc={server?.country_code ?? ""} />
      <span className="vpn-dmain">
        <b>{d.name}</b>
        <small>{server ? placeLine(server) : "Server removed"} · {ago(d.last_handshake)} · {bytes(d.rx_bytes + d.tx_bytes)} this month</small>
        {d.paused_until && <small className="vpn-dpaused">Paused until {resumeDay(d.paused_until)}: monthly data used up</small>}
        {err && <small className="site-error">{err}</small>}
      </span>
      {ask ? (
        <span className="vpn-confirm">
          <button className="site-textbutton vpn-danger" type="button" disabled={busy} onClick={() => void remove()}>{busy ? "Deleting…" : "Delete for good"}</button>
          <button className="site-textbutton" type="button" disabled={busy} onClick={() => setAsk(false)}>Cancel</button>
        </span>
      ) : (
        <button className="site-textbutton vpn-danger" type="button" aria-label={`Delete ${d.name}`} onClick={() => setAsk(true)}>Delete</button>
      )}
    </li>
  );
}

/** One compact line on the Passport (like the VIP Trading Bot line): locked / unlocked, linking to /vpn. Hidden until it's open. */
export function VpnStatus() {
  const [s, setS] = useState<VpnState | null>(null);
  useEffect(() => { void memberApi<VpnState>("/members/vpn").then(setS).catch(() => undefined); }, []);
  if (!s || !s.enabled) return null;
  const open = s.eligible;
  return (
    <a className={`pp-vip${open ? " is-open" : ""}`} href={open ? "/vpn/devices" : "/vpn"} aria-label={open ? "Kiwi VPN: My Devices" : "Kiwi VPN"}>
      <span className="pp-vip-icon" aria-hidden>{open ? "✓" : "🔒"}</span>
      <span className="pp-vip-text"><b>Kiwi VPN</b>
        <small>{open ? `${s.devices.length} / ${s.max_devices} devices${planOf(s).kind === "free" ? " · free while we test" : ""}` : s.reason || "Not available"}</small></span>
      <span className="pp-vip-go">{open ? "My Devices →" : "View →"}</span>
    </a>
  );
}
