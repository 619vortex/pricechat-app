import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import QRCode from "qrcode";
import { MemberApiError, memberApi, refreshSession } from "../../../website/members";
import { usd } from "../format";
import { portfolioApi, type Holding } from "./api";
import { GradeBadge, PortfolioFrame, errorText } from "./shared";

/** "Scan with your phone": the computer shows a QR code; the phone opens it, is signed in to the same account (one-time,
 * 10-minute code) and lands in the camera scanner. Cards scanned on the phone appear here as they're added. */

interface Handoff { id: number; url: string; expires_at: string; minutes: number }

export function PhoneScanButton({ className = "pf-btn pf-big", label = "📱 Scan with your phone", onClosed }: {
  className?: string; label?: string; onClosed?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={`${className} pf-desktop-only`} onClick={() => setOpen(true)}>{label}</button>
      {open && <PhoneQr onClose={() => { setOpen(false); onClosed?.(); }} />}
    </>
  );
}

function PhoneQr({ onClose }: { onClose: () => void }) {
  const [h, setH] = useState<Handoff | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [state, setState] = useState<"pending" | "connected" | "expired" | "error">("pending");
  const [device, setDevice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [added, setAdded] = useState<Holding[]>([]);
  const known = useRef<Set<number> | null>(null);
  const [left, setLeft] = useState(0);

  const start = useCallback(async () => {
    setErr(null); setState("pending"); setQr(null); setDevice(null);
    try {
      const n = await memberApi<Handoff>("/members/portfolio/handoff", { method: "POST" });
      setH(n);
      setQr(await QRCode.toDataURL(n.url, { width: 560, margin: 1, errorCorrectionLevel: "M", color: { dark: "#05120c", light: "#ffffff" } }));
    } catch (e) {
      setErr(errorText(e)); setState("error");
    }
  }, []);
  useEffect(() => { void start(); }, [start]);
  useEffect(() => {
    void portfolioApi.dashboard().then((d) => { known.current = new Set(d.holdings.map((x) => x.id)); }).catch(() => { known.current = new Set(); });
  }, []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);

  // waiting for the phone: status every 2 s; once connected, the new cards every 4 s
  useEffect(() => {
    if (!h) return;
    const tick = async () => {
      setLeft(Math.max(0, Math.round((Date.parse(h.expires_at) - Date.now()) / 1000)));
      if (state === "pending") {
        try {
          const s = await memberApi<{ status: string; device: string | null }>(`/members/portfolio/handoff/${h.id}`);
          if (s.status === "connected") { setState("connected"); setDevice(s.device); }
          else if (s.status === "expired" || s.status === "replaced") setState("expired");
        } catch { /* keep waiting */ }
      } else if (state === "connected" && known.current) {
        try {
          const d = await portfolioApi.dashboard();
          setAdded(d.holdings.filter((x) => !known.current!.has(x.id)).sort((a, b) => Date.parse(b.added_at) - Date.parse(a.added_at)));
        } catch { /* keep polling */ }
      }
    };
    void tick();
    const t = window.setInterval(() => void tick(), state === "connected" ? 4000 : 2000);
    return () => window.clearInterval(t);
  }, [h, state]);

  return (
    <div className="pf-sheet-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="pf-sheet pf-qr" role="dialog" aria-modal="true" aria-label="Scan with your phone">
        <button type="button" className="pf-x" aria-label="Close" onClick={onClose}>×</button>
        {state !== "connected" ? (
          <div className="pf-qr-grid">
            <div className="pf-qr-code">
              {qr ? <img src={qr} alt="QR code: open it with your phone's camera" width={280} height={280} /> : <div className="pf-qr-wait"><span className="pf-spinner" /></div>}
              {state === "expired" && <div className="pf-qr-expired"><p>This code expired.</p><button type="button" className="pf-shiny" onClick={() => void start()}>Show a new code</button></div>}
            </div>
            <div>
              <h2>Scan cards with your phone</h2>
              <ol className="pf-qr-steps">
                <li>Open your phone's <b>camera</b> and point it at this code.</li>
                <li>Tap the link that appears: Price Chat opens, already signed in to your portfolio.</li>
                <li>Allow the camera, then photograph your cards. Each one appears here.</li>
              </ol>
              {state === "pending" && h && <p className="pf-fine"><span className="pf-spinner" /> Waiting for your phone… code valid for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}. It works once and only for you.</p>}
              {err && <p className="pf-error">{err}</p>}
            </div>
          </div>
        ) : (
          <div className="pf-qr-live">
            <h2>✅ {device ? `Your ${device} is connected` : "Phone connected"}</h2>
            <p className="pf-muted">Scan cards on your phone. They appear here as soon as you add them.</p>
            {added.length === 0 ? <p className="pf-qr-wait-line"><span className="pf-spinner" /> Waiting for your first card…</p> : (
              <ul className="pf-qr-added">
                {added.map((x) => (
                  <li key={x.id}>
                    {x.card.image_url ? <img src={x.photos[0] ? portfolioApi.photoUrl(x.photos[0].id) : x.card.image_url} alt="" /> : <span className="pf-qr-noimg" />}
                    <span><b>{x.card.name}</b><small>{x.card.year} · {x.card.set_name}{x.card.edition !== "—" ? ` · ${x.card.edition}` : ""}</small></span>
                    <GradeBadge h={x} />
                    <b>{x.value?.each != null ? usd(x.value.each) : "—"}</b>
                  </li>
                ))}
              </ul>
            )}
            <div className="pf-actions"><button type="button" className="pf-shiny pf-big" onClick={onClose}>Done</button></div>
          </div>
        )}
      </section>
    </div>
  );
}

/** The phone side: /pokemon/portfolio/phone/:token signs this browser in (once) and opens the scanner. */
export function PhoneLinkPage() {
  const { token = "" } = useParams();
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    void memberApi<{ username: string }>("/members/portfolio/handoff/claim", { method: "POST", body: JSON.stringify({ token }) })
      .then((r) => {
        refreshSession();
        try { sessionStorage.setItem("pc-phone-scan", r.username); } catch { /* fine */ }
        nav("/pokemon/portfolio/add?scan=1", { replace: true });
      })
      .catch((e) => setErr(e instanceof MemberApiError ? e.message : "Couldn't connect. Check your connection and scan the code again."));
  }, [token, nav]);
  return (
    <PortfolioFrame crumbs={[["Markets", "/pokemon"], ["Scan with your phone", null]]}>
      <section className="pf-empty">
        {err ? (
          <><h1>Couldn't connect</h1><p>{err}</p><p className="pf-fine">On your computer: My Portfolio › “Scan with your phone” shows a fresh code.</p></>
        ) : <><h1><span className="pf-spinner" /> Connecting to your portfolio…</h1><p className="pf-muted">The camera opens in a moment.</p></>}
      </section>
    </PortfolioFrame>
  );
}
