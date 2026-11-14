import { useState } from "react";
import type { UpdateInfo } from "./vpn";

/** What installing does to the connection, said before it happens (docs/AUTO-UPDATE.md, "Tunnel during an update"). */
export function updateWarning(killSwitch: boolean, strict: boolean): string {
  const base = "You'll be disconnected for about 30 seconds while Kiwi VPN updates.";
  if (strict) return `${base} The strict kill switch keeps blocking meanwhile, so you'll be offline until Kiwi VPN reconnects.`;
  if (killSwitch) return `${base} The kill switch is off during the update too, so apps may use your normal connection meanwhile. Kiwi VPN reconnects afterwards.`;
  return `${base} Apps may use your normal connection meanwhile. Kiwi VPN reconnects afterwards.`;
}

/**
 * 2.2: "new version available", with an Install button. The service found and checked the update (signed manifest, and before it
 * runs anything, the pinned code-signing certificate, the hash and the version); this only shows what it says and asks.
 * While connected, Install first says what happens to the connection.
 */
export function UpdateBanner({ info, connected, killSwitch, strict, install }: { info: UpdateInfo | null; connected: boolean; killSwitch: boolean; strict: boolean; install: () => void }) {
  const [asking, setAsking] = useState(false);
  if (!info) return null;
  const version = info.version ? `Kiwi VPN ${info.version}` : "A new version";
  if (info.state === "downloading") return <div className="update" role="status"><b>Downloading {version}…</b></div>;
  if (info.state === "installing") return <div className="update" role="status"><b>Installing {version}.</b> <span>Kiwi VPN restarts in a moment.</span></div>;
  if (info.state === "failed") return info.error ? <div className="update is-failed" role="alert"><span>{info.error}</span></div> : null;
  if (info.state !== "available" && info.state !== "required") return null;
  const required = info.state === "required";
  const go = () => { setAsking(false); install(); };
  return (
    <div className={`update${required || info.security ? " is-urgent" : ""}`} role="status" aria-label="Update">
      <b>{required ? `Update required: ${version}` : `New version available: ${version}`}</b>
      {info.security && <span> This update fixes a security problem.</span>}
      {info.notes && <small className="notes">{info.notes}</small>}
      {asking ? (
        <>
          <p className="hint">{updateWarning(killSwitch, strict)}</p>
          <button type="button" className="row" onClick={go}>Install now</button>
          <button type="button" className="link" onClick={() => setAsking(false)}>Later</button>
        </>
      ) : (
        <button type="button" className="row" onClick={() => (connected ? setAsking(true) : go())}>Install</button>
      )}
    </div>
  );
}
