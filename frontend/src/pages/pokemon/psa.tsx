import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Loading, relativeTime } from "../../components/ui";
import { pokemonApi, type PCard, type PsaLinkResult } from "./api";
import { num } from "./format";

const CERT_FIELDS: [string, string][] = [
  ["cert_number", "Cert"], ["grade", "Grade"], ["year", "Year"], ["brand", "Brand"], ["card_number", "Number"], ["subject", "Subject"],
  ["variety", "Variety"], ["spec_id", "PSA SpecID"], ["total_population", "Population at this grade"], ["population_higher", "Graded higher"],
];

/** What PSA said about one slab: certificate-specific, never card-wide population. */
export function CertFacts({ cert }: { cert: Record<string, string | number> }) {
  return (
    <dl className="pm-source-facts">
      {CERT_FIELDS.filter(([k]) => cert[k] !== undefined).map(([k, label]) => (
        <div key={k}><dt>{label}</dt><dd>{String(cert[k])}</dd></div>
      ))}
    </dl>
  );
}

/** Settings: PSA connection status, a live test, and a population refresh for linked cards. */
export function PsaConnectionCard() {
  const { data, failure, setData } = useApi(() => pokemonApi.sources());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const psa = data?.sources.find((s) => s.key === "psa");

  async function run(action: () => Promise<{ ok: boolean; text: string }>) {
    setBusy(true);
    setMsg(null);
    try {
      setMsg(await action());
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "Failed." });
    } finally {
      setBusy(false);
      setData(await pokemonApi.sources());
    }
  }

  if (!psa) return <Card title="PSA connection">{failure ? <ApiErrorBanner error={failure} /> : <Loading />}</Card>;
  return (
    <Card title={<span className="pm-source-title">PSA connection <Badge tone={psa.connected ? "good" : "neutral"}>{psa.connected ? "KEY PRESENT" : "NO KEY"}</Badge></span>}>
      <p className="muted small">
        Official PSA Public API: cert verification and card-wide population by PSA spec. The key is read on the backend from{" "}
        <span className="mono">PSA_API_KEY</span> (set it on <Link to="/api-keys">API keys</Link>) and never reaches this page. PSA gives no prices or sales.
      </p>
      {!psa.connected && <p className="pm-source-reason">{psa.reason}</p>}
      <dl className="pm-source-facts">
        <div><dt>Calls today</dt><dd>{psa.requests_today} / 100 (free tier)</dd></div>
        <div><dt>Last success</dt><dd>{psa.last_success_at ? relativeTime(psa.last_success_at) : "never"}</dd></div>
        <div><dt>Last error</dt><dd className={psa.last_error ? "pm-down" : undefined}>{psa.last_error ?? "none"}</dd></div>
        <div><dt>Population rows imported</dt><dd>{num(psa.records_imported)}</dd></div>
      </dl>
      <div className="btn-group">
        <button type="button" className="btn btn-primary" disabled={busy || !psa.connected}
                onClick={() => void run(async () => { const r = await pokemonApi.testSource("psa"); return { ok: r.ok, text: r.message }; })}>
          Test PSA connection
        </button>
        <button type="button" className="btn" disabled={busy || !psa.connected}
                onClick={() => void run(async () => {
                  const r = await pokemonApi.syncSource("psa");
                  return r.error ? { ok: false, text: String(r.error) } : { ok: true, text: `Refreshed ${r.refreshed} of ${r.linked} linked cards (${r.population_rows} grade counts).` };
                })}>
          Refresh PSA population
        </button>
      </div>
      {msg && <div className={`banner ${msg.ok ? "banner-good" : "banner-error"}`} role="status">{msg.ok ? "✓" : "✕"} {msg.text}</div>}
    </Card>
  );
}

/** Card page: link this card to PSA through one cert, then pull its card-wide PSA population. */
export function PsaLinkBox({ card, onChanged }: { card: PCard; onChanged: () => void }) {
  const [cert, setCert] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<PsaLinkResult | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function link(confirm: boolean) {
    setBusy(true);
    setError(null);
    try {
      const r = await pokemonApi.psaLink(cert, card.id, confirm);
      setRes(r);
      if (r.linked) onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      await pokemonApi.psaRefresh(card.id);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="PSA">
      {card.psa_spec ? (
        <p className="small">
          Linked to PSA spec <span className="mono">{card.psa_spec}</span>
          {card.psa_certs?.length ? <> via cert {card.psa_certs.map((c) => <span key={c} className="mono"> {c}</span>)}</> : null}.{" "}
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void refresh()}>Refresh population</button>
        </p>
      ) : (
        <p className="muted small">Not linked to PSA yet. Enter the cert number of any PSA slab of exactly this card: PSA's SpecID from that cert links the card and brings in its population per grade.</p>
      )}
      <form className="btn-group" onSubmit={(e) => { e.preventDefault(); void link(false); }}>
        <input className="input mono" inputMode="numeric" placeholder="PSA cert number" value={cert} onChange={(e) => setCert(e.target.value)} aria-label="PSA cert number" />
        <button type="submit" className="btn btn-primary" disabled={busy || !cert.trim()}>{card.psa_spec ? "Add cert" : "Link PSA cert"}</button>
      </form>
      <ApiErrorBanner error={error} />
      {res && (
        <div className="pm-psa-result">
          <h3 className="pm-pop-title">Certificate {String(res.cert.cert_number ?? "")} (this slab only)</h3>
          <CertFacts cert={res.cert} />
          {res.linked ? (
            <p className="pm-up small">
              ✓ Linked.{" "}
              {res.population ? `PSA population stored for ${res.population.grades_stored} grades (card-wide, all copies of this spec).` : `Population: ${res.population_error}`}
            </p>
          ) : res.match.status === "review" && res.match.card_id === card.id ? (
            <p className="small">
              Not certain: {res.match.reasons.join(" · ")}{" "}
              <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void link(true)}>It is this card: link</button>
            </p>
          ) : (
            <p className="pm-down small">Not linked: {res.match.status === "not_in_catalog" ? "this cert is a card that isn't in the catalog" : res.match.reasons.join(" · ")}.</p>
          )}
        </div>
      )}
    </Card>
  );
}
