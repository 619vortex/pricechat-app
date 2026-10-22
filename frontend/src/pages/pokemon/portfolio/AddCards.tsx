import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { usd } from "../format";
import { portfolioApi, shrink, type Candidate, type Holding, type PCardBrief, type ScanResult } from "./api";
import { AddForm, CardArt, PortfolioFrame, SignInPrompt, cardLine, errorText, useMember, useStatus } from "./shared";
import { PhoneScanButton } from "./PhoneHandoff";

function phoneScanFor(): string | null {
  try { return sessionStorage.getItem("pc-phone-scan"); } catch { return null; }
}

type Step = "pick" | "camera" | "identifying" | "results" | "confirm" | "back" | "saved";

/** Add Cards: scan with the camera (or a photo), or search; confirm the exact printing; Raw or Graded; save; scan another. */
export function AddCardsPage() {
  const member = useMember();
  const status = useStatus();
  const [sp, setSp] = useSearchParams();
  const [step, setStep] = useState<Step>("pick");
  const [pick, setPick] = useState<Candidate | null>(null);
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [back, setBack] = useState<Blob | null>(null);
  const [backPreview, setBackPreview] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [chosen, setChosen] = useState<PCardBrief | null>(null);
  const [saved, setSaved] = useState<Holding | null>(null);
  const [keepPhoto, setKeepPhoto] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [count, setCount] = useState(0);
  const fromScan = useRef(false);
  const previewUrl = useRef<string | null>(null);

  // a card handed over by the market card page ("+ Add to Portfolio")
  useEffect(() => {
    const id = Number(sp.get("card"));
    if (!id) return;
    void fetch(`/api/pokemon/cards/${id}`).then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (d?.card) { setChosen(d.card as PCardBrief); fromScan.current = false; setStep("confirm"); }
    }).catch(() => undefined);
  }, [sp]);
  useEffect(() => {
    if (sp.get("scan") !== "1" || !member || status === null) return;
    const next = new URLSearchParams(sp);
    next.delete("scan");
    setSp(next, { replace: true });
    if (status.scanner) setStep("camera");
  }, [sp, setSp, member, status]);
  useEffect(() => () => { if (previewUrl.current) URL.revokeObjectURL(previewUrl.current); }, []);
  // each new step starts at the top of the scanner (phones: nothing important below the fold)
  useEffect(() => {
    if (step !== "camera" && step !== "back") document.querySelector(".pf-add")?.scrollIntoView?.({ block: "start" });
  }, [step]);

  const identify = useCallback(async (blob: Blob) => {
    setErr(null);
    const small = await shrink(blob, 1600, 0.88);
    setPhoto(small);
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = URL.createObjectURL(small);
    setPreview(previewUrl.current);
    setStep("identifying");
    try {
      const r = await portfolioApi.scan(small);
      setResult(r);
      fromScan.current = true;
      setStep("results");
    } catch (e) {
      setErr(errorText(e));
      setStep("pick");
    }
  }, []);

  const reset = () => {
    setPhoto(null); setBack(null); setResult(null); setChosen(null); setSaved(null); setErr(null); setPick(null);
    if (backPreview) URL.revokeObjectURL(backPreview);
    setBackPreview(null);
    setSp({}, { replace: true });
  };
  const takeBack = async (blob: Blob) => {
    const small = await shrink(blob, 1600, 0.88);
    setBack(small);
    if (backPreview) URL.revokeObjectURL(backPreview);
    setBackPreview(URL.createObjectURL(small));
    setStep("confirm");
  };
  const afterSave = async (h: Holding) => {
    if (fromScan.current && keepPhoto && status?.photos) {
      // the photos are optional: the card is saved even if an upload fails (one at a time: small requests, small memory)
      if (photo) { try { await portfolioApi.uploadPhoto(h.id, photo, "front"); } catch { /* optional */ } }
      if (back) { try { await portfolioApi.uploadPhoto(h.id, back, "back"); } catch { /* optional */ } }
    }
    setSaved(h);
    setCount((c) => c + 1);
    setStep("saved");
  };

  const crumbs: [string, string | null][] = [["Markets", "/pokemon"], ["My Portfolio", "/pokemon/portfolio"], ["Add Cards", null]];
  if (member === undefined) return <PortfolioFrame crumbs={crumbs}><p className="pf-muted">Loading…</p></PortfolioFrame>;
  if (member === null) return <PortfolioFrame crumbs={crumbs}><SignInPrompt title="Sign in to add cards" /></PortfolioFrame>;
  const graded = result?.detected.graded;
  return (
    <PortfolioFrame crumbs={crumbs}>
      <section className="pf-add">
        {phoneScanFor() && <p className="pf-phone-banner">📱 Scanning into <b>{phoneScanFor()}</b>'s portfolio. Cards appear on your computer as you add them.</p>}
        {count > 0 && <p className="pf-ok">{count} card{count === 1 ? "" : "s"} added this session.</p>}
        {err && <p className="pf-error" role="alert">{err}</p>}

        {step === "pick" && (
          <>
            <ScanStart onCamera={() => setStep("camera")} onFile={(f) => void identify(f)} scanner={status?.scanner !== false} perDay={status?.scans_per_day} />
            <SearchAdd onChoose={(c) => { setChosen(c); fromScan.current = false; setStep("confirm"); }} />
          </>
        )}

        {step === "camera" && <Camera label="Front of the card" onCapture={(b) => void identify(b)} onCancel={() => setStep("pick")} />}
        {step === "back" && <Camera label="Back of the card (optional)" onCapture={(b) => void takeBack(b)} onCancel={() => setStep("confirm")} />}

        {step === "identifying" && (
          <div className="pf-identifying">
            {preview && <img src={preview} alt="Your photo" />}
            <p><span className="pf-spinner" aria-hidden /> Identifying the card…</p>
          </div>
        )}

        {step === "results" && result && (
          <div className="pf-results">
            <div className="pf-results-head">
              {preview && <img src={preview} alt="Your photo" className="pf-results-photo" />}
              <div>
                <h2>{result.candidates.length ? (result.needs_choice ? "Which card is it?" : "We think it's this card") : "No match found"}</h2>
                {result.needs_choice && result.reason && <p className="pf-muted">{result.reason.charAt(0).toUpperCase() + result.reason.slice(1)}.</p>}
                {graded && <p className="pf-ok">Graded slab detected: {graded.company} {graded.grade}{graded.cert ? ` · cert ${graded.cert}` : ""} (please confirm)</p>}
              </div>
            </div>
            <CandidateList list={result.candidates} onChoose={(c) => { setChosen(c.card); setPick(c); setStep("confirm"); }} />
            <div className="pf-actions">
              <button type="button" className="pf-btn" onClick={() => setStep("camera")}>Retake photo</button>
              <button type="button" className="pf-btn" onClick={() => setStep("pick")}>Search instead</button>
            </div>
          </div>
        )}

        {/* kept mounted while the back photo is taken: the chosen grade, quantity and details survive the trip to the camera */}
        {(step === "confirm" || step === "back") && chosen && (
          <div className="pf-confirm" hidden={step === "back"}>
            <div className="pf-confirm-card">
              <CardArt card={chosen} />
              <div>
                {fromScan.current && <p className="pf-eyebrow">✅ Card Identified</p>}
                <h2>{chosen.name}</h2>
                <dl className="pf-idfacts">
                  <div><dt>Set</dt><dd>{chosen.set_name} · {chosen.year}</dd></div>
                  <div><dt>Number</dt><dd>{chosen.card_number ?? "—"}</dd></div>
                  <div><dt>Edition</dt><dd>{chosen.edition !== "—" ? chosen.edition : "Standard"}{chosen.variant ? ` · ${chosen.variant}` : ""}</dd></div>
                  {chosen.language !== "English" && <div><dt>Language</dt><dd>{chosen.language}</dd></div>}
                  {fromScan.current && result && <div><dt>Match</dt><dd>{pick?.suggested ? "High confidence" : result.confidence === "likely" ? "Likely" : "Low confidence: you chose it"}</dd></div>}
                </dl>
                {pick?.warning && <p className="pf-error">⚠️ {pick.warning}</p>}
                {graded && <p className="pf-fine">Check the slab label: it should name this set, number and printing{result?.detected.label?.reverse ? " (it says Reverse Foil)" : result?.detected.label?.first ? " (it says 1st Edition)" : ""}. A label identifies the card, it doesn't prove authenticity.</p>}
                <div className="pf-actions">
                  <button type="button" className="pf-btn pf-sm" onClick={() => setStep(result ? "results" : "pick")}>Change Card</button>
                  {fromScan.current && <button type="button" className="pf-btn pf-sm" onClick={() => { setPick(null); setStep("camera"); }}>Retake Photo</button>}
                </div>
              </div>
            </div>
            <AddForm card={chosen} source={fromScan.current ? "scan" : "manual"}
                     initial={graded ? { kind: "graded", grading_company: graded.company as "PSA", grade: graded.grade, cert_number: graded.cert } : undefined}
                     onSaved={(h) => void afterSave(h)} onCancel={() => setStep(result ? "results" : "pick")} />
            {fromScan.current && photo && status?.photos && (
              <div className="pf-shots">
                <figure>{preview && <img src={preview} alt="Front photo" />}<figcaption>Front</figcaption></figure>
                {backPreview ? <figure><img src={backPreview} alt="Back photo" /><figcaption>Back <button type="button" className="pf-link" onClick={() => setStep("back")}>retake</button></figcaption></figure>
                  : <button type="button" className="pf-addback" onClick={() => setStep("back")}>＋ Back photo<small>optional</small></button>}
                <label className="pf-check"><input type="checkbox" checked={keepPhoto} onChange={(e) => setKeepPhoto(e.target.checked)} /> Save my photos with the card (private unless the card is public)</label>
              </div>
            )}
          </div>
        )}

        {step === "saved" && saved && (
          <div className="pf-saved">
            <div className="pf-saved-burst" aria-hidden>✨</div>
            <h2>Added to your portfolio</h2>
            <p><b>{saved.card.name}</b> · {saved.label}{saved.quantity > 1 ? ` ×${saved.quantity}` : ""}</p>
            <p>{saved.value?.each != null ? <>Estimated {usd(saved.value.each)}{saved.value.low != null && saved.value.high != null && saved.value.low !== saved.value.high ? ` (${usd(saved.value.low)}–${usd(saved.value.high)})` : ""}</> : "Price unavailable for now"}</p>
            <div className="pf-actions pf-center">
              <button type="button" className="pf-shiny pf-big" onClick={() => { reset(); setStep(fromScan.current ? "camera" : "pick"); }}>{fromScan.current ? "📷 Scan Another" : "＋ Add Another"}</button>
              <Link className="pf-btn pf-big" to="/pokemon/portfolio">View Portfolio</Link>
            </div>
          </div>
        )}
      </section>
    </PortfolioFrame>
  );
}

function ScanStart({ onCamera, onFile, scanner, perDay }: { onCamera: () => void; onFile: (f: File) => void; scanner: boolean; perDay?: number }) {
  const take = useRef<HTMLInputElement>(null);
  const pick = useRef<HTMLInputElement>(null);
  if (!scanner) return <p className="pf-muted">The scanner isn't available right now: add cards by search below.</p>;
  const live = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
  return (
    <div className="pf-scanstart">
      <div className="pf-desktop-only pf-qr-hint">
        <p><b>On a computer?</b> Use your phone's camera: it's sharper and quicker.</p>
        <PhoneScanButton className="pf-shiny pf-huge" />
        <p className="pf-fine">or use this computer's webcam / a photo file:</p>
      </div>
      <button type="button" className="pf-shiny pf-huge pf-scan-main" onClick={() => (live ? onCamera() : take.current?.click())}>📷 Scan Pokémon Card</button>
      <div className="pf-actions pf-center">
        <button type="button" className="pf-btn" onClick={() => take.current?.click()}>Take a photo</button>
        <button type="button" className="pf-btn" onClick={() => pick.current?.click()}>Upload from gallery</button>
      </div>
      <input ref={take} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
      <input ref={pick} type="file" accept="image/jpeg,image/png,image/webp,image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
      <p className="pf-fine">Lay the card flat, fill the frame, avoid glare. Graded slabs work too.{perDay ? ` Up to ${perDay} scans a day.` : ""}</p>
    </div>
  );
}

/** Live camera (getUserMedia) with a card-shaped guide. Falls back to the phone's own camera via a file input. */
const CAMERA_PROBLEM: Record<string, string> = {
  NotAllowedError: "Camera access was declined. To scan live, allow the camera for pricechat.live (iPhone: Settings › Safari › Camera; Android: tap the lock icon › Permissions). Or use “Take a photo”, which opens your camera app.",
  SecurityError: "Camera access was declined. To scan live, allow the camera for pricechat.live in your browser settings, or use “Take a photo”.",
  NotFoundError: "No camera was found on this device. Use “Take a photo” or upload a picture.",
  NotReadableError: "The camera is busy in another app or tab. Close it and try again, or use “Take a photo”.",
  AbortError: "The camera stopped unexpectedly. Try again, or use “Take a photo”.",
};

/** The rear camera, live (getUserMedia, facingMode environment), with a card-shaped guide. Every failure ends in a usable
 * path: the phone's own camera through a file input (capture="environment"), which works in every mobile browser. */
function Camera({ onCapture, onCancel, label }: { onCapture: (b: Blob) => void; onCancel: () => void; label: string }) {
  const video = useRef<HTMLVideoElement>(null);
  const take = useRef<HTMLInputElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let alive = true;
    let s: MediaStream | null = null;
    const stop = () => { s?.getTracks().forEach((t) => t.stop()); s = null; };
    if (!window.isSecureContext) {
      setProblem("The live camera needs a secure (https) page. Use “Take a photo” instead.");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setProblem("This browser can't show a live camera. “Take a photo” opens your camera app instead.");
      return;
    }
    const attach = (st: MediaStream) => {
      if (!alive) { st.getTracks().forEach((t) => t.stop()); return; }
      s = st;
      setStream(st);
      const v = video.current;
      if (v) {
        v.srcObject = st;
        v.setAttribute("playsinline", ""); // iOS Safari: play inline, never full screen
        void v.play().catch(() => undefined);
      }
    };
    // as many pixels as the camera offers (recognition wants ~1500-2500 px; phones give 1080p-4K)
    const strict = { video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 }, height: { ideal: 1440 } }, audio: false };
    void navigator.mediaDevices.getUserMedia(strict)
      .catch((e: DOMException) => {
        // some Android cameras refuse the resolution hints: retry with the rear camera alone
        if (e?.name === "OverconstrainedError" || e?.name === "ConstraintNotSatisfiedError" || e?.name === "TypeError")
          return navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
        throw e;
      })
      .then(attach)
      .catch((e: DOMException) => alive && setProblem(CAMERA_PROBLEM[e?.name] ?? "The camera couldn't start. Use “Take a photo” instead."));
    // a backgrounded tab must not keep the camera (and its light) on
    const onHide = () => { if (document.visibilityState === "hidden") { stop(); setStream(null); setProblem("The camera was paused. Tap “Retry” to scan again."); } };
    document.addEventListener("visibilitychange", onHide);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { alive = false; stop(); document.removeEventListener("visibilitychange", onHide); document.body.style.overflow = prevOverflow; };
  }, []);
  const guide = useRef<HTMLElement>(null);
  const capture = () => {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const W = v.videoWidth, H = v.videoHeight;
    // exactly what the card guide shows on screen (the video is cropped to fill the screen: object-fit cover), plus a
    // small margin so the card's edges are never cut; mapped back to the camera's own pixels
    let sx = 0, sy = 0, sw = W, sh = H;
    const vr = v.getBoundingClientRect(), gr = guide.current?.getBoundingClientRect();
    if (gr && vr.width > 0 && vr.height > 0) {
      const scale = Math.max(vr.width / W, vr.height / H);
      const x0 = (W - vr.width / scale) / 2, y0 = (H - vr.height / scale) / 2;
      const m = 0.06;
      sx = x0 + (gr.left - vr.left - gr.width * m) / scale;
      sy = y0 + (gr.top - vr.top - gr.height * m) / scale;
      sw = (gr.width * (1 + 2 * m)) / scale;
      sh = (gr.height * (1 + 2 * m)) / scale;
      sx = Math.max(0, sx); sy = Math.max(0, sy); sw = Math.min(W - sx, sw); sh = Math.min(H - sy, sh);
      if (Math.min(sw, sh) < 700) { sx = 0; sy = 0; sw = W; sh = H; } // too few pixels inside the guide: send the whole frame
    }
    const c = document.createElement("canvas");
    c.width = Math.round(sw);
    c.height = Math.round(sh);
    c.getContext("2d")!.drawImage(v, sx, sy, sw, sh, 0, 0, c.width, c.height);
    c.toBlob((b) => { if (b) { stream?.getTracks().forEach((t) => t.stop()); onCapture(b); } }, "image/jpeg", 0.92);
  };
  return (
    <div className="pf-camera" role="dialog" aria-modal="true" aria-label={label}>
      {problem ? (
        <div className="pf-camera-problem">
          <p>{problem}</p>
          <div className="pf-actions pf-center">
            <button type="button" className="pf-shiny pf-big" onClick={() => take.current?.click()}>📷 Take a photo</button>
            {problem.includes("paused") && <button type="button" className="pf-btn pf-big" onClick={() => { setProblem(null); onCancel(); }}>Retry</button>}
          </div>
        </div>
      ) : (
        <div className="pf-viewfinder">
          <video ref={video} playsInline muted autoPlay onLoadedData={() => setReady(true)} />
          <div className="pf-guide" aria-hidden><i ref={guide} /></div>
          <p className="pf-guide-label">{label}</p>
          <p className="pf-guide-hint">{ready ? "Fit the card inside the frame, then tap the button" : "Starting the camera…"}</p>
        </div>
      )}
      <input ref={take} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onCapture(f); e.target.value = ""; }} />
      <div className="pf-camera-bar">
        <button type="button" className="pf-btn" onClick={() => { stream?.getTracks().forEach((t) => t.stop()); onCancel(); }}>Cancel</button>
        {!problem && <button type="button" className="pf-shutter" aria-label="Capture photo" onClick={capture} disabled={!stream || !ready} />}
        <button type="button" className="pf-btn" onClick={() => take.current?.click()}>Photo</button>
      </div>
    </div>
  );
}

function CandidateList({ list, onChoose }: { list: Candidate[]; onChoose: (c: Candidate) => void }) {
  if (!list.length) return <p className="pf-muted">We couldn't match this photo to a catalogue card. Try again with the whole card in frame and no glare, or search for it.</p>;
  return (
    <ul className="pf-cands">
      {list.map((c) => (
        <li key={c.card.id}>
          <button type="button" className={c.suggested ? "is-suggested" : ""} onClick={() => onChoose(c)}>
            <CardArt card={c.card} />
            <span className="pf-cand-text">
              <b>{c.card.name}</b>
              <small>{cardLine(c.card)}</small>
              {c.warning && <small className="pf-error">⚠️ {c.warning}</small>}
              <small className="pf-muted">{Object.entries(c.reference_prices).map(([k, v]) => `${k} ${usd(v, true)}`).join(" · ") || "No market price yet"}</small>
            </span>
            {c.suggested && <span className="pf-tag is-public">Best match</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

function SearchAdd({ onChoose }: { onChoose: (c: PCardBrief) => void }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Candidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const t = window.setTimeout(() => {
      setBusy(true);
      void portfolioApi.search(q).then((r) => setRes(r.results)).catch(() => setRes([])).finally(() => setBusy(false));
    }, 300);
    return () => window.clearTimeout(t);
  }, [q]);
  return (
    <div className="pf-search">
      <h2>Or search the catalogue</h2>
      <input className="pf-searchbox" type="search" placeholder='e.g. "Charizard base set 1st edition" or "Lugia neo genesis"' value={q} onChange={(e) => setQ(e.target.value)}
             autoComplete="off" aria-label="Search cards" />
      {busy && <p className="pf-muted">Searching…</p>}
      {res && <CandidateList list={res} onChoose={(c) => onChoose(c.card)} />}
    </div>
  );
}
