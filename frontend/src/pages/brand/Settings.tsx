import { useEffect, useState, type ReactNode } from "react";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading, relativeTime } from "../../components/ui";
import { bm, brandPatch, type Brand, type BrandResponse, type Strategy } from "./api";

type Section = "general" | "content" | "publishing" | "ai" | "safety";
const SECTIONS: [Section, string][] = [["general", "General"], ["content", "Content"], ["publishing", "Publishing"], ["ai", "AI"], ["safety", "Safety"]];

export function SettingsTab() {
  const { data, failure, setData } = useApi(bm.brand);
  const [section, setSection] = useState<Section>("general");
  const [draft, setDraft] = useState<BrandResponse | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { if (data) setDraft(data); }, [data]);
  if (!draft || !data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const st = draft.strategy;
  const set = <K extends keyof Brand>(k: K, v: Brand[K]) => setDraft({ ...draft, [k]: v });
  const setS = <K extends keyof Strategy>(k: K, v: Strategy[K]) => setDraft({ ...draft, strategy: { ...st, [k]: v } });
  const save = async () => {
    setError(null);
    try {
      const r = await bm.saveBrand(brandPatch(draft));  // only the editable fields, never the read-only catalogs
      setData({ ...data, ...r });
      setSaved("Saved.");
      window.setTimeout(() => setSaved(null), 2500);
    } catch (e) {
      setError(e);
    }
  };
  const lines = (v: string[]) => v.join("\n");
  const unlines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

  return (
    <div className="bmx-settings">
      <nav className="bmx-chips">{SECTIONS.map(([k, l]) => <button key={k} className={section === k ? "is-on" : ""} onClick={() => setSection(k)}>{l}</button>)}</nav>
      <ApiErrorBanner error={error} />
      {section === "general" && (
        <Card title="Brand profile">
          <Row label="Brand name"><input value={draft.name} onChange={(e) => set("name", e.target.value)} /></Row>
          <Row label="Website"><input value={draft.website} onChange={(e) => set("website", e.target.value)} /></Row>
          <Row label="Business description"><textarea rows={3} value={draft.description} onChange={(e) => set("description", e.target.value)} /></Row>
          <Row label="Target audience"><textarea rows={2} value={draft.audience} onChange={(e) => set("audience", e.target.value)} /></Row>
          <Row label="Industry"><input value={draft.industry} onChange={(e) => set("industry", e.target.value)} /></Row>
          <Row label="Brand personality"><textarea rows={2} value={draft.personality} onChange={(e) => set("personality", e.target.value)} /></Row>
          <Row label="Main topics" hint="one per line"><textarea rows={3} value={lines(draft.topics)} onChange={(e) => set("topics", unlines(e.target.value))} /></Row>
          <Row label="Topics to avoid" hint="one per line; also blocks scripts that mention them"><textarea rows={3} value={lines(draft.avoid)} onChange={(e) => set("avoid", unlines(e.target.value))} /></Row>
          <Row label="Brand colors" hint="hex, one per line: accent, background"><textarea rows={2} value={lines(draft.colors)} onChange={(e) => set("colors", unlines(e.target.value))} /></Row>
          <Row label="Logo URL" hint="https; only shown if the watermark is on"><input value={draft.logo_url} onChange={(e) => set("logo_url", e.target.value)} /></Row>
          <Row label="Call to action"><input value={draft.cta} onChange={(e) => set("cta", e.target.value)} /></Row>
          <Row label="Competitor accounts" hint="for inspiration only: never copied"><textarea rows={2} value={lines(draft.competitors)} onChange={(e) => set("competitors", unlines(e.target.value))} /></Row>
          <Row label="Approved examples" hint="scripts or captions in your voice; the AI learns the tone (one per line)">
            <textarea rows={4} value={draft.examples.map((x) => x.text).join("\n")} onChange={(e) => set("examples", unlines(e.target.value).map((text) => ({ text, approved: true })))} />
          </Row>
          <Row label="Language"><input value={draft.language} onChange={(e) => set("language", e.target.value)} /></Row>
          <Row label="Time zone"><input value={st.timezone} onChange={(e) => setS("timezone", e.target.value)} placeholder="Asia/Dubai" /></Row>
        </Card>
      )}
      {section === "content" && (
        <>
          <Card title="What to make">
            <Row label="Content categories">
              <div className="bmx-checks">{Object.entries(data.categories).map(([k, c]) => (
                <label key={k}><input type="checkbox" checked={st.categories.includes(k)} onChange={(e) => setS("categories", e.target.checked ? [...st.categories, k] : st.categories.filter((x) => x !== k))} /> {c.label}</label>
              ))}</div>
            </Row>
            <Row label="Videos per day"><input type="number" min={1} max={10} value={st.videos_per_day} onChange={(e) => setS("videos_per_day", Number(e.target.value))} /></Row>
            <Row label="Length (seconds)">
              <span className="bmx-inline"><input type="number" min={10} max={120} value={st.min_seconds} onChange={(e) => setS("min_seconds", Number(e.target.value))} /> to
                <input type="number" min={10} max={120} value={st.max_seconds} onChange={(e) => setS("max_seconds", Number(e.target.value))} /></span>
            </Row>
            <Row label="Voice engine" hint="auto = the most natural voice the worker has (Kokoro, a local neural voice; the Windows voice only as a last resort)">
              <select value={st.voice} onChange={(e) => setS("voice", e.target.value)}>
                {[["auto", "auto (best natural voice)"], ["kokoro", "kokoro (local neural)"], ["openai", "openai (cloud)"], ["piper", "piper (local)"], ["system", "system (robotic)"]]
                  .map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Row>
            <Row label="Narrator" hint="the Kokoro voice that reads every video">
              <select value={st.voice_name} onChange={(e) => setS("voice_name", e.target.value)}>
                {Object.entries(data.voices ?? { [st.voice_name]: st.voice_name }).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </Row>
            <Row label="Speaking speed" hint="1.0 = normal; 1.1 suits short videos">
              <span className="bmx-inline"><input type="range" min={0.8} max={1.4} step={0.05} value={st.voice_speed} onChange={(e) => setS("voice_speed", Number(e.target.value))} /> {st.voice_speed.toFixed(2)}×</span>
            </Row>
          </Card>
          <Card title="Hashtags">
            <p className="bmx-fine">Each video gets: your always-used tags → tags for its own story (the coin, the card, the PSA grade) → the writer's
              suggestion → the category's tags, up to the maximum. Hype tags (#fyp, #tothemoon, #100x…) are never used.</p>
            <Row label="Always-used hashtags" hint="your brand tag"><input value={st.hashtags.always.join(" ")} onChange={(e) => setS("hashtags", { ...st.hashtags, always: e.target.value.split(/\s+/).filter(Boolean) })} /></Row>
            {Object.entries(data.categories).map(([k, c]) => (
              <Row key={k} label={c.label}>
                <input value={(st.hashtags.per_category[k] ?? []).join(" ")} placeholder="#tag #tag"
                       onChange={(e) => setS("hashtags", { ...st.hashtags, per_category: { ...st.hashtags.per_category, [k]: e.target.value.split(/\s+/).filter(Boolean) } })} />
              </Row>
            ))}
            <Row label="Max hashtags" hint="TikTok works best with 3-5"><input type="number" min={0} max={10} value={st.hashtags.max} onChange={(e) => setS("hashtags", { ...st.hashtags, max: Number(e.target.value) })} /></Row>
          </Card>
          <Card title="Video">
            <Row label="Call to action at the end"><input type="checkbox" checked={st.outro_cta} onChange={(e) => setS("outro_cta", e.target.checked)} /></Row>
            <Row label="Background music" hint="https URL of a track you hold a licence for; empty = none"><input value={st.music} onChange={(e) => setS("music", e.target.value)} /></Row>
          </Card>
          <Card title="Templates">
            {Object.entries(data.templates).map(([k, t]) => (
              <Row key={k} label={t.label}>
                <span className="bmx-inline">
                  <input type="color" value={st.templates[k]?.accent ?? t.accent} onChange={(e) => setS("templates", { ...st.templates, [k]: { ...(st.templates[k] ?? t), accent: e.target.value } })} />
                  <input value={st.templates[k]?.banner ?? t.banner} maxLength={24} onChange={(e) => setS("templates", { ...st.templates, [k]: { ...(st.templates[k] ?? t), banner: e.target.value } })} />
                  <span className="muted">{t.scenes.join(" → ")}</span>
                </span>
              </Row>
            ))}
          </Card>
        </>
      )}
      {section === "publishing" && (
        <Card title="Publishing">
          <Row label="Posting times" hint="local time, one per line (HH:MM)"><textarea rows={3} value={lines(st.post_times)} onChange={(e) => setS("post_times", unlines(e.target.value))} /></Row>
          <Row label="How videos reach TikTok">
            <select value={st.publish_mode} onChange={(e) => setS("publish_mode", e.target.value as Strategy["publish_mode"])}>
              <option value="drafts">Drafts: sent to my TikTok inbox, I post from the app (fully automatic)</option>
              <option value="direct">Direct: I approve each post on TikTok's posting screen</option>
            </select>
          </Row>
          <Row label="Approval required" hint="every video waits in Content Studio until you approve it"><input type="checkbox" checked={st.approval_required} onChange={(e) => setS("approval_required", e.target.checked)} /></Row>
          <Row label="TikTok audited this app" hint="only after TikTok approved the audit: allows public direct posts"><input type="checkbox" checked={st.tiktok_audited} onChange={(e) => setS("tiktok_audited", e.target.checked)} /></Row>
          <Row label="Label AI-assisted content"><input type="checkbox" checked={st.ai_disclosure} onChange={(e) => setS("ai_disclosure", e.target.checked)} /></Row>
          <Row label="Logo watermark" hint="TikTok's guidelines don't allow apps to add brand watermarks to shared videos: leave off unless you're sure">
            <input type="checkbox" checked={st.watermark} onChange={(e) => setS("watermark", e.target.checked)} />
          </Row>
        </Card>
      )}
      {section === "ai" && (
        <>
          <Card title="AI engines">
            <Row label="Script writer"><select value={st.llm} onChange={(e) => setS("llm", e.target.value)}>
              <option value="auto">Auto: local model, then cloud fallback, then template</option><option value="ollama">Local model only (Ollama on the worker)</option>
              <option value="anthropic">Cloud (Anthropic key on the worker)</option><option value="template">Template only (no AI)</option></select></Row>
            <Row label="Local model"><input value={st.ollama_model} onChange={(e) => setS("ollama_model", e.target.value)} /></Row>
            <Row label="Video quality"><select value={st.quality} onChange={(e) => setS("quality", e.target.value)}>{["draft", "standard", "high"].map((q) => <option key={q}>{q}</option>)}</select></Row>
            <Row label="Monthly cloud cost limit (USD)"><input type="number" min={0} value={st.monthly_cost_limit_usd} onChange={(e) => setS("monthly_cost_limit_usd", Number(e.target.value))} /></Row>
          </Card>
          <Workers />
        </>
      )}
      {section === "safety" && (
        <>
          <Card title="Safety checks (always on for every script)">
            <ul className="bmx-facts">
              <li>Every number spoken or shown must come from the research (prices, %, sales, dates).</li>
              <li>No predictions, advice or hype phrases (“guaranteed”, “to the moon”, “will hit”…). Finance videos say “Not financial advice.”</li>
              <li>Duplicate detection: the same topic isn't made twice within the window below.</li>
              <li>Copyright: no stock footage, no other creators' content; only our own graphics, our data, licensed music you provide.</li>
            </ul>
            <Row label="Banned topics" hint="one per line"><textarea rows={4} value={lines(st.banned_topics)} onChange={(e) => setS("banned_topics", unlines(e.target.value))} /></Row>
            <Row label="Require sources"><input type="checkbox" checked={st.require_sources} onChange={(e) => setS("require_sources", e.target.checked)} /></Row>
            <Row label="Duplicate window (days)"><input type="number" min={1} max={90} value={st.duplicate_days} onChange={(e) => setS("duplicate_days", Number(e.target.value))} /></Row>
            <Row label="Risk level"><select value={st.risk_level} onChange={(e) => setS("risk_level", e.target.value)}><option value="low">Low: facts only</option><option value="medium">Medium</option></select></Row>
          </Card>
          <Emergency />
        </>
      )}
      <div className="bmx-save"><button className="btn btn-primary" onClick={() => void save()}>Save settings</button>{saved && <span className="muted">{saved}</span>}</div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return <section className="bmx-card"><h2>{title}</h2><div className="bmx-rows">{children}</div></section>;
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="bmx-row"><span>{label}{hint && <small>{hint}</small>}</span><div>{children}</div></label>;
}

function Workers() {
  const { data, reload } = useApi(bm.workers, 15000);
  const [name, setName] = useState("DGX Spark");
  const [created, setCreated] = useState<{ token: string; command: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const add = async () => {
    setError(null);
    try { const r = await bm.addWorker(name); setCreated(r); await reload(); } catch (e) { setError(e); }
  };
  return (
    <section className="bmx-card">
      <h2>AI workers <span className="muted">· {data ? `${data.queued} queued · ${data.running} rendering` : ""}</span></h2>
      <p className="bmx-fine">Workers render videos on their own machine (the DGX Spark) and connect out to Price Chat with a token: the Spark exposes nothing.
        If no worker is online, videos wait safely in the queue. Setup: <code>worker/README.md</code>.</p>
      <ApiErrorBanner error={error} />
      <ul className="bmx-list">
        {data?.workers.map((w) => (
          <li key={w.id}>
            <span className={`bmx-dot ${w.online ? "is-on" : ""}`} /><b>{w.name}</b>{w.cloud && <span className="muted"> (cloud fallback)</span>}
            <span className="muted"> · {w.online ? "online" : `last seen ${relativeTime(w.last_seen_at)}`}{w.info?.tts ? ` · voice: ${(w.info.tts as string[]).join(", ") || "none"}` : ""}
              {Array.isArray(w.info?.llm) ? ` · models: ${(w.info.llm as string[]).slice(0, 3).join(", ") || "none"}` : ""}</span>
            <span className="bmx-actions">
              <button className="btn btn-sm" onClick={() => void bm.setWorker(w.id, !w.enabled).then(reload)}>{w.enabled ? "Disable" : "Enable"}</button>
              <button className="btn btn-sm btn-ghost" onClick={() => { if (window.confirm(`Remove ${w.name}? Its token stops working.`)) void bm.deleteWorker(w.id).then(reload); }}>Remove</button>
            </span>
          </li>
        ))}
      </ul>
      <div className="bmx-inline"><input value={name} onChange={(e) => setName(e.target.value)} aria-label="Worker name" /><button className="btn" onClick={() => void add()}>Add worker</button></div>
      {created && (
        <div className="bmx-note">
          <p><b>Copy this now: the token is shown only once.</b> Run on the worker machine:</p>
          <pre className="bmx-pre">{created.command}</pre>
        </div>
      )}
    </section>
  );
}

function Emergency() {
  const { data, setData } = useApi(bm.overview);
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const on = data.automation.emergency_stop;
  const flip = async () => {
    setBusy(true);
    try { const a = await bm.emergency(!on); setData({ ...data, automation: a }); } finally { setBusy(false); }
  };
  return (
    <section className={`bmx-card ${on ? "bmx-danger" : ""}`}>
      <h2>Emergency stop</h2>
      <p>{on ? "ON: nothing renders and nothing is sent to TikTok until you release it." : "Stops AUTO, cancels every queued job and holds every finished video. Use it if anything looks wrong."}</p>
      <button className={`btn ${on ? "" : "btn-danger"}`} disabled={busy} onClick={() => void flip()}>{on ? "Release emergency stop" : "EMERGENCY STOP"}</button>
    </section>
  );
}
