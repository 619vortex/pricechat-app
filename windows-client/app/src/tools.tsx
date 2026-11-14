import { useState } from "react";
import { type LeakReport, type Settings, type SpeedReport, leakCheck, speedTest } from "./vpn";
import { allowedIpsExcluding, leakVerdict, mbps, speedSummary, validateExclusions } from "./smart";

/** When the normal kill switch lets traffic out unprotected (audit M3). Kept in step with RELEASE_CASES in service/src/protocol.rs. */
export const RELEASE_CASES = [
  "for about 3 seconds while Windows restarts the Kiwi VPN service after a crash",
  "while Windows starts, before the Kiwi VPN service is running",
  "after the tunnel fails to restart 6 times in a row",
  "10 minutes after a restart if the server never answers",
  "2 minutes after a server switch that never completes",
  "while Kiwi VPN is being updated",
];

/** The service accepts at most this many routes in a config (service/src/config.rs MAX_ALLOWED). */
const MAX_ROUTES = 64;

export function Switch({ label, hint, on, off, onChange }: { label: string; hint?: string; on: boolean; off?: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className={`toggle${off ? " is-off" : ""}`}>
      <span><b>{label}</b>{hint && <small>{hint}</small>}</span>
      <input type="checkbox" role="switch" aria-label={label} checked={on} disabled={off} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

/** Kiwi VPN 2.0's connection settings. Each one says plainly what it does and what it can't do. */
export function ConnectionSettings({ prefs, change, connected, killSwitchReady, strictReady = false, dotReady = false }: { prefs: Settings; change: (p: Partial<Settings>) => void; connected: boolean; killSwitchReady: boolean; strictReady?: boolean; dotReady?: boolean }) {
  const [split, setSplit] = useState((prefs.split_exclude ?? []).join("\n"));
  const checked = validateExclusions(split.split(/\r?\n/), ["10.66.0.1/32", "9.9.9.9/32", "149.112.112.112/32"]);
  const pieces = allowedIpsExcluding(checked.ok).length;
  if (pieces > MAX_ROUTES) checked.errors.push(`These ranges split the connection into ${pieces} pieces; Kiwi VPN allows ${MAX_ROUTES}. Use fewer or wider ranges.`);
  const saved = (prefs.split_exclude ?? []).join("\n");
  return (
    <div className="toggles" aria-label="Connection">
      <h2>Connection</h2>
      <Switch label="Smart Connect" hint="Connect picks the fastest server that isn't busy right now." on={prefs.smart_connect === true} onChange={(v) => change({ smart_connect: v })} />
      <Switch label="Switch servers if one stops answering" hint="Tries the same server again first. The kill switch keeps traffic blocked while it switches." on={prefs.failover === true} onChange={(v) => change({ failover: v })} />
      {killSwitchReady ? (
        <>
          <Switch label="Kill switch" hint="Blocks all internet traffic outside the VPN while you want to be connected, even if the app closes. Disconnect, or turn this off, to get your normal connection back."
                  on={!!prefs.kill_switch} onChange={(v) => change({ kill_switch: v })} />
          <Switch label="Allow my local network" hint="Printers and other devices on your Wi-Fi stay reachable while the kill switch is on." on={prefs.allow_lan !== false} off={!prefs.kill_switch} onChange={(v) => change({ allow_lan: v })} />
          {strictReady && (
            <Switch label="Strict kill switch" hint="Also blocks while the service restarts, while Windows starts and when Kiwi VPN gives up reconnecting. Only Disconnect gets your normal connection back. If Kiwi VPN can't reconnect, you stay offline until you press Disconnect."
                    on={!!prefs.strict_kill_switch} off={!prefs.kill_switch} onChange={(v) => change({ strict_kill_switch: v })} />
          )}
          {dotReady && (
            <Switch label="Block DNS over TLS outside the VPN" hint="Stops apps sending encrypted DNS (port 853) around the tunnel, also to your local network and the addresses that skip the VPN."
                    on={!!prefs.block_dot} off={!prefs.kill_switch} onChange={(v) => change({ block_dot: v })} />
          )}
          <div className="releases" aria-label="When the kill switch lets traffic through">
            <small>{prefs.kill_switch && prefs.strict_kill_switch && strictReady
              ? "The strict kill switch keeps blocking in all of these cases, which the normal kill switch lets through:"
              : "The normal kill switch lets your traffic out unprotected:"}</small>
            <ul>{RELEASE_CASES.map((c) => <li key={c}>{c}</li>)}</ul>
            <small>It never blocks after you press Disconnect or turn it off.</small>
          </div>
        </>
      ) : (
        // Never offered until the service proves it can do it: a kill switch that doesn't block is worse than none.
        <div className="toggle is-off"><span><b>Kill switch</b><small>Not available yet. Your traffic isn't blocked if the tunnel drops.</small></span></div>
      )}
      <Switch label="Block malware and phishing sites" hint="Uses Quad9's filtering DNS through the tunnel instead of the server's own resolver. Quad9 sees the names you look up, not your address." on={!!prefs.block_malware} onChange={(v) => change({ block_malware: v })} />
      <div className="split">
        <b>Addresses that skip the VPN</b>
        <small>One per line, like 192.168.1.0/24. Everything else, and all IPv6, stays in the tunnel. Applies next time you connect.</small>
        <textarea aria-label="Addresses that skip the VPN" rows={3} value={split} onChange={(e) => setSplit(e.target.value)} spellCheck={false} />
        {checked.errors.map((e) => <p key={e} className="problem" role="alert">{e}</p>)}
        <button type="button" className="row" disabled={checked.errors.length > 0 || split.trim() === saved.trim()}
                onClick={() => change({ split_exclude: checked.ok.map((c) => `${c.ip >>> 24}.${(c.ip >>> 16) & 255}.${(c.ip >>> 8) & 255}.${c.ip & 255}/${c.bits}`) })}>
          Save{connected ? " (reconnect to apply)" : ""}
        </button>
      </div>
    </div>
  );
}

/** Speed test and leak check. Both are run on request only, and both say what they used. */
export function Tools({ connected, normalIp }: { connected: boolean; normalIp: string | null }) {
  const [speed, setSpeed] = useState<SpeedReport | null>(null);
  const [leak, setLeak] = useState<LeakReport | null>(null);
  const [busy, setBusy] = useState<"speed" | "leak" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const go = async (kind: "speed" | "leak") => {
    setBusy(kind); setProblem(null);
    try { if (kind === "speed") setSpeed(await speedTest()); else setLeak(await leakCheck()); }
    catch (e) { setProblem(String(e)); }
    finally { setBusy(null); }
  };
  const verdict = leak ? leakVerdict(leak, normalIp) : null;
  return (
    <section className="tools" aria-label="Tools">
      <h2>Speed test</h2>
      <p className="hint">{connected ? "Measures the VPN connection." : "You're not connected: this measures your normal connection."} Uses Cloudflare's speed test (about 25 MB).</p>
      <button type="button" className="row" disabled={busy !== null} onClick={() => void go("speed")}>{busy === "speed" ? "Testing…" : "Run speed test"}</button>
      {speed && <p className="result" role="status">{speedSummary({ pingMs: speed.ping_ms, downMbps: mbps(speed.down_bytes, speed.down_ms), upMbps: mbps(speed.up_bytes, speed.up_ms) })}</p>}
      <h2>Leak check</h2>
      <p className="hint">Checks your public IPv4 and IPv6 addresses and which DNS servers answer your lookups.</p>
      <button type="button" className="row" disabled={busy !== null || !connected} onClick={() => void go("leak")}>{busy === "leak" ? "Checking…" : connected ? "Check for leaks" : "Connect first"}</button>
      {verdict && (
        <ul className={`leaks${verdict.ok ? " is-ok" : " is-bad"}`} role="status">
          {verdict.lines.map((l) => <li key={l.text} className={l.ok ? "ok" : "bad"}>{l.text}</li>)}
        </ul>
      )}
      {problem && <p className="problem" role="alert">{problem}</p>}
    </section>
  );
}
