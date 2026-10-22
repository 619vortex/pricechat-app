import { useEffect, useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Loading, relativeTime } from "../../components/ui";
import { marketApi, pokemonApi, type Listing, type PreviewRow, type ScrydexLog, type Source } from "./api";
import { MarketShell } from "./mx";
import { DASH, day, sourceLabel, usd } from "./format";
import type { Diagnostics } from "./portfolio/api";

const POP_EXAMPLE = `[
  {"card_id": 1, "grading_company": "PSA", "grade": "10", "count": 121, "as_of": "2026-10-01", "source": "manual"}
]`;

/** Pokémon data sources: who is connected, what each may legally provide, imports. Credentials are never shown. */
export function PokemonSources() {
  const { data, failure, loading, setData } = useApi(() => pokemonApi.sources());
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Data Sources", null]]}>
    <div className="pm">
      <header>
        <h1 className="mx-h1">Pokémon data sources</h1>
      </header>
      <p className="muted pm-intro">
        Price Chat only uses data a source provides legally to this account. Nothing is scraped and nothing is estimated where data is missing:
        unknown values stay “—”.
      </p>
      <ApiErrorBanner error={failure} />
      {loading && !data ? <Loading /> : data && (
        <>
          <div className="pm-sources">
            {data.sources.map((s) => <SourceCard key={s.key} s={s} onSaved={(sources) => setData({ sources })} />)}
          </div>
          <PortfolioDiagnostics />
          <CsvImporter />
          <ReviewQueue />
          <div className="pm-two">
            <ImportBox kind="population" example={POP_EXAMPLE} />
          </div>
          <PsaCert connected={!!data.sources.find((s) => s.key === "psa")?.connected} />
        </>
      )}
    </div>
    </MarketShell>
  );
}

function SourceCard({ s, onSaved }: { s: Source; onSaved: (sources: Source[]) => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await request<{ sources: Source[] }>(`/pokemon/sources/${s.key}/credentials`, {
        method: "PUT", body: JSON.stringify({ values: Object.fromEntries(Object.entries(values).map(([k, v]) => [k.toLowerCase(), v])) }),
      });
      setValues({});
      onSaved(r.sources);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }
  async function testIt() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await pokemonApi.testSource(s.key);
      setMsg(`${r.ok ? "✓" : "✕"} ${r.message}`);
      onSaved((await pokemonApi.sources()).sources);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Test failed.");
    } finally {
      setBusy(false);
    }
  }
  async function runSync() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await pokemonApi.syncSource(s.key as "ebay" | "pricecharting");
      setMsg(r.error ? String(r.error) : "Synced.");
      onSaved((await pokemonApi.sources()).sources);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Sync failed.");
    } finally {
      setBusy(false);
    }
  }
  async function importTcgdex() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await pokemonApi.importTcgdex(100);
      setMsg(r.error ? r.error : `${r.tcgdex_cards} cards: ${r.created} new, ${r.linked_existing + r.japanese_linked} matched to existing cards, ${r.images_added} images added.`);
      onSaved((await pokemonApi.sources()).sources);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Couldn't reach TCGdex.");
    } finally {
      setBusy(false);
    }
  }
  async function images() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await pokemonApi.fetchImages();
      setMsg(r.error ? r.error : `${r.updated} card images added${r.not_found.length ? `, ${r.not_found.length} not found` : ""}.`);
      onSaved((await pokemonApi.sources()).sources);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Couldn't reach the Pokémon TCG API.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title={<span className="pm-source-title">{s.name} <Badge tone={s.connected ? "good" : "neutral"}>{s.connected ? "CONNECTED" : "NOT CONNECTED"}</Badge></span>}>
      {!s.connected && s.reason && <p className="pm-source-reason">{s.reason}</p>}
      {s.notes.map((n) => <p key={n} className={`pm-source-note${n.startsWith("SOLD DATA UNAVAILABLE") || n.includes("NOT available") ? " is-warn" : ""}`}>{n}</p>)}
      <dl className="pm-source-facts">
        <div><dt>Status</dt><dd>{s.connected ? "Connected" : "Not connected"}</dd></div>
        <div><dt>Credentials present</dt><dd>{s.credentials_present === null ? "none needed" : s.credentials_present ? "YES" : "NO"}</dd></div>
        <div><dt>Last request</dt><dd>{s.last_request_at ? `${relativeTime(s.last_request_at)} (${s.requests_today} today)` : DASH}</dd></div>
        <div><dt>Last success</dt><dd>{s.last_success_at ? relativeTime(s.last_success_at) : DASH}</dd></div>
        <div><dt>Last error</dt><dd className={s.last_error ? "pm-down" : undefined}>{s.last_error ?? DASH}</dd></div>
        <div><dt>Records</dt><dd>{s.records_imported}</dd></div>
        <div><dt>Rate limit</dt><dd>{s.rate_limit ?? DASH}</dd></div>
        <div><dt>Provides</dt><dd>{s.serves.length ? s.serves.map((m) => m.replace(/_/g, " ")).join(", ") : "nothing usable"}</dd></div>
        <div><dt>Access</dt><dd>{s.access}</dd></div>
        <div><dt>Licence</dt><dd>{s.licence}</dd></div>
      </dl>
      {s.credentials.length > 0 && (
        <form className="pm-cred" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          {s.credentials.map((c) => (
            <label key={c}>
              <span className="muted">{c}{s.missing.includes(c) ? "" : " (saved)"}</span>
              <input type="password" autoComplete="off" value={values[c] ?? ""} placeholder={s.missing.includes(c) ? "paste to connect" : "•••••• saved: paste to replace"}
                     onChange={(e) => setValues({ ...values, [c]: e.target.value })} />
            </label>
          ))}
          <button type="submit" className="btn" disabled={busy || !Object.values(values).some((v) => v.trim())}>Save (encrypted)</button>
        </form>
      )}
      <div className="pm-source-actions">
        {s.key !== "tcgplayer" && s.key !== "beckett" && (
          <button type="button" className="btn" disabled={busy} onClick={() => void testIt()}>Test connection</button>
        )}
        {s.key === "pokemontcg" && <button type="button" className="btn" disabled={busy} onClick={() => void images()}>Sync now (card images)</button>}
        {s.key === "tcgdex" && <button type="button" className="btn" disabled={busy} onClick={() => void importTcgdex()}>Import 100 vintage cards</button>}
      {(s.key === "ebay" || s.key === "pricecharting") && s.connected && (
        <button type="button" className="btn" disabled={busy} onClick={() => void runSync()}>Sync now</button>
      )}
      </div>
      {msg && <p className="pm-source-msg" role="status">{msg}</p>}
      {s.key === "pricecharting" && <PriceChartingPanel connected={s.connected} onDone={async () => onSaved((await pokemonApi.sources()).sources)} />}
      {s.key === "scrydex" && <ScrydexPanel connected={s.connected} onDone={async () => onSaved((await pokemonApi.sources()).sources)} />}
    </Card>
  );
}

/** A dollar value from a sync log: Scrydex logs {market}, PriceCharting logs a plain number. */
function logPrice(v: { market: number | null } | number | undefined): string {
  const n = typeof v === "number" ? v : v?.market;
  return n !== null && n !== undefined ? usd(Math.round(n * 100)) : DASH;
}

/** PriceCharting: exact-product sync (paced ~1 request/second) and its per-card log. Values are references, not sales. */
function PriceChartingPanel({ connected, onDone }: { connected: boolean; onDone: () => Promise<void> }) {
  const [logs, setLogs] = useState<ScrydexLog[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const load = () => pokemonApi.syncLogs("pricecharting").then((r) => setLogs(r.logs)).catch(() => setLogs([]));
  useEffect(() => { void load(); }, []);
  async function run() {
    setBusy(true);
    setError(null);
    try {
      await pokemonApi.syncSource("pricecharting");
      await load();
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed.");
    } finally {
      setBusy(false);
    }
  }
  const last = logs?.[0];
  const entries = last ? (showAll ? last.entries : last.entries.filter((e) => e.status !== "matched").concat(last.entries.filter((e) => e.status === "matched").slice(0, 8))) : [];
  return (
    <div className="pm-scrydex">
      <div className="pm-source-actions">
        <button type="button" className="btn btn-primary" disabled={busy || !connected} onClick={() => void run()}>
          {busy ? "Syncing… (~1 request/second)" : "Sync PriceCharting"}
        </button>
      </div>
      {error && <p className="pm-down pm-small" role="alert">{error}</p>}
      {last && (
        <div className="pm-small">
          <p className="pm-source-msg"><b>Last sync</b> {day(last.started_at)}: <b className={last.status === "ok" ? "pm-up" : last.status === "error" ? "pm-down" : undefined}>{last.status.toUpperCase()}</b>
            {" · "}{last.requests} requests · {last.summary.matched ?? 0} cards matched exactly · {last.summary.psa10_prices ?? 0} with a PSA 10 value
            {" · "}{last.summary.unmatched ?? 0} unmatched · {last.summary.ambiguous ?? 0} ambiguous{last.error && <span className="pm-down"> · {last.error}</span>}</p>
          <table className="table pm-table">
            <thead><tr><th>Card</th><th>PriceCharting product</th><th>Status</th><th className="pm-num">PSA 10</th><th className="pm-num">BGS 10</th><th className="pm-num">CGC 10</th>
              <th className="pm-num">Grade 9 (any)</th><th className="pm-num">Ungraded</th><th className="pm-num">PC sales count</th></tr></thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={`${e.card_id}-${i}`}>
                  <td>{e.card}</td><td className="mono">{e.product ?? e.reason ?? DASH}</td>
                  <td className={e.status === "matched" ? "pm-up" : "pm-down"}>{e.status}</td>
                  {(["PSA 10", "BGS 10", "CGC 10", "Grade 9", "Ungraded"] as const).map((g) => <td key={g} className="pm-num">{logPrice(e.prices?.[g])}</td>)}
                  <td className="pm-num">{e.sales_volume ?? DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {last.entries.length > entries.length && <button type="button" className="mx-clear" onClick={() => setShowAll(true)}>Show all {last.entries.length} cards</button>}
        </div>
      )}
    </div>
  );
}

const TEST_CARDS: [string, string, string][] = [["Charizard", "Base Set", "1st Edition"], ["Blastoise", "Base Set", "1st Edition"], ["Lugia", "Neo Genesis", "1st Edition"]];

/** Scrydex: sync the three test cards first, then everything; every run's per-card log. Requests = credits. */
function ScrydexPanel({ connected, onDone }: { connected: boolean; onDone: () => Promise<void> }) {
  const [logs, setLogs] = useState<ScrydexLog[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = () => pokemonApi.syncLogs("scrydex").then((r) => setLogs(r.logs)).catch(() => setLogs([]));
  useEffect(() => { void load(); }, []);
  async function run(all: boolean) {
    setBusy(true);
    setError(null);
    try {
      let ids: number[] | undefined;
      if (!all) {
        const m = await marketApi.market({ era: "all", limit: 250 });
        ids = TEST_CARDS.map(([n, set, ed]) => m.rows.find((r) => r.card.name === n && r.card.set_name === set && r.card.edition === ed)?.card.id)
          .filter((x): x is number => typeof x === "number");
      } else if (!window.confirm("Sync every English catalog card with Scrydex? About 1 request (= 1 credit) per card, plus 1 per PSA 10 price history, capped per run.")) {
        return;
      }
      await pokemonApi.scrydexSync(ids, true);
      await load();
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed.");
    } finally {
      setBusy(false);
    }
  }
  const last = logs?.[0];
  return (
    <div className="pm-scrydex">
      <div className="pm-source-actions">
        <button type="button" className="btn btn-primary" disabled={busy || !connected} onClick={() => void run(false)}>Sync 3 test cards</button>
        <button type="button" className="btn" disabled={busy || !connected || !last || last.status === "error"} onClick={() => void run(true)}
                title={!last ? "Run the 3-card test first" : undefined}>Sync all English cards</button>
      </div>
      {error && <p className="pm-down pm-small" role="alert">{error}</p>}
      {last && (
        <div className="pm-small">
          <p className="pm-source-msg"><b>Last sync</b> {day(last.started_at)}: <b className={last.status === "ok" ? "pm-up" : last.status === "error" ? "pm-down" : undefined}>{last.status.toUpperCase()}</b>
            {" · "}{last.requests} requests (credits) · {last.cache_hits} from cache · {last.summary.matched_variants ?? 0} variants matched · {last.summary.valuations ?? 0} valuations
            {" · "}{last.summary.population_rows ?? 0} population rows · {last.summary.history_points ?? 0} history points · {last.summary.unmatched ?? 0} unmatched
            {last.error && <span className="pm-down"> · {last.error}</span>}</p>
          <table className="table pm-table">
            <thead><tr><th>Card</th><th>Scrydex</th><th>Status</th><th className="pm-num">PSA 10</th><th className="pm-num">PSA 9</th><th className="pm-num">PSA 8</th><th className="pm-num">PSA 10 pop</th><th>Note</th></tr></thead>
            <tbody>
              {last.entries.map((e, i) => (
                <tr key={`${e.card_id}-${i}`}>
                  <td>{e.card}</td><td className="mono">{e.scrydex_id ?? DASH}{e.variant ? ` · ${e.variant}` : ""}</td>
                  <td className={e.status === "matched" ? "pm-up" : "pm-down"}>{e.status}</td>
                  {(["PSA 10", "PSA 9", "PSA 8"] as const).map((g) => <td key={g} className="pm-num">{logPrice(e.prices?.[g])}</td>)}
                  <td className="pm-num">{e.population?.["PSA 10"] ?? DASH}</td>
                  <td className="muted">{e.reason ?? e.match ?? ""}{typeof e.history_points === "number" ? ` · ${e.history_points} history points` : ""}{e.history ? ` · ${e.history}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ImportBox({ kind, example }: { kind: "sales" | "population"; example: string }) {
  const [text, setText] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [errors, setErrors] = useState<{ row: number; error: string }[]>([]);
  async function run() {
    setResult(null);
    setErrors([]);
    let rows: unknown;
    try {
      rows = JSON.parse(text);
    } catch {
      setResult("That isn't valid JSON: paste a list of rows like the example.");
      return;
    }
    if (!Array.isArray(rows)) {
      setResult("Paste a JSON list ([ … ]).");
      return;
    }
    try {
      const r = kind === "sales" ? await pokemonApi.importSales(rows) : await pokemonApi.importPopulation(rows);
      setResult(`${r.imported} imported${"duplicates" in r ? `, ${r.duplicates} duplicates skipped` : ""}, ${r.errors.length} rejected.`);
      setErrors(r.errors);
    } catch (e) {
      setResult(e instanceof Error ? e.message : "Import failed.");
    }
  }
  return (
    <Card title={kind === "sales" ? "Import sales (manual)" : "Import population (manual)"}>
      <p className="muted pm-small">
        {kind === "sales" ? "Only sales you have the right to use. Duplicates (same source + listing id, or same card, grade, price, day and source) are skipped. Imported sales are unverified unless marked verified."
          : "Population per card and grade from a source you may use. A newer report replaces an older one from the same source."}
      </p>
      <textarea className="pm-import" rows={7} value={text} placeholder={example} onChange={(e) => setText(e.target.value)} aria-label={`${kind} rows (JSON)`} />
      <button type="button" className="btn" disabled={!text.trim()} onClick={() => void run()}>Import</button>
      {result && <p className="pm-source-msg" role="status">{result}</p>}
      {errors.length > 0 && <ul className="pm-errors">{errors.slice(0, 20).map((e) => <li key={e.row}>Row {e.row}: {e.error}</li>)}</ul>}
    </Card>
  );
}

function PsaCert({ connected }: { connected: boolean }) {
  const [cert, setCert] = useState("");
  const [out, setOut] = useState<Record<string, string | number> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card title="PSA cert verification">
      <p className="muted pm-small">PSA's official public API verifies one slab by cert number (card, grade, label). It has no population or price data.</p>
      <form className="pm-cred" onSubmit={(e) => {
        e.preventDefault();
        setErr(null);
        setOut(null);
        void pokemonApi.psaCert(cert).then((r) => setOut(r.cert)).catch((x) => setErr(x instanceof Error ? x.message : "Lookup failed."));
      }}>
        <label><span className="muted">Cert number</span><input value={cert} onChange={(e) => setCert(e.target.value)} inputMode="numeric" /></label>
        <button type="submit" className="btn" disabled={!connected || !cert.trim()}>{connected ? "Verify" : "Connect PSA first"}</button>
      </form>
      {err && <p className="pm-down" role="alert">{err}</p>}
      {out && <dl className="pm-source-facts">{Object.entries(out).map(([k, v]) => <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></div>)}</dl>}
    </Card>
  );
}

const CSV_HEADER = "card,set,year,card_number,language,edition,variant,grading_company,grade,cert_number,sale_price,currency,sale_date,source,source_listing_id,url,auction_house";

/** Admin sales importer: CSV -> preview (match, validate, duplicates) -> confirm -> import. */
export function CsvImporter() {
  const [csv, setCsv] = useState("");
  const [rows, setRows] = useState<PreviewRow[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [picked, setPicked] = useState<Record<number, boolean>>({});
  const [choice, setChoice] = useState<Record<number, number>>({});
  const [verified, setVerified] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function preview(text = csv) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await pokemonApi.csvPreview(text);
      setRows(r.rows);
      setCounts(r.counts);
      setPicked(Object.fromEntries(r.rows.filter((x) => x.status === "ready").map((x) => [x.row, true])));
      setChoice({});
    } catch (e) {
      setRows(null);
      setMsg(e instanceof Error ? e.message : "Couldn't read the file.");
    } finally {
      setBusy(false);
    }
  }
  async function commit() {
    if (!rows) return;
    setBusy(true);
    try {
      const accept = rows.filter((r) => picked[r.row]).map((r) => ({ row: r.row, card_id: choice[r.row] ?? null }));
      const r = await pokemonApi.csvCommit(csv, accept, verified);
      setMsg(`${r.imported} imported · ${r.skipped} skipped · ${r.failed} failed`);
      setRows(null);
      setCounts(null);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  }
  const selectable = (r: PreviewRow) => r.status === "ready" || (r.status === "review" && !!choice[r.row]);
  return (
    <Card title="Import sales (CSV)">
      <p className="muted pm-small">
        Columns: <code>{CSV_HEADER}</code>. Dates as YYYY-MM-DD. Other currencies convert to USD at the ECB rate of the sale date. Only sales you
        have the right to use. Rows are previewed first: nothing is saved until you confirm.
      </p>
      <div className="pm-csv-input">
        <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void f.text().then((t) => { setCsv(t); void preview(t); });
        }} />
        <textarea className="pm-import" rows={5} value={csv} placeholder={`${CSV_HEADER}\n…`} onChange={(e) => setCsv(e.target.value)} aria-label="CSV text" />
        <button type="button" className="btn" disabled={busy || !csv.trim()} onClick={() => void preview()}>Preview</button>
      </div>
      {msg && <p className="pm-source-msg" role="status">{msg}</p>}
      {rows && counts && (
        <>
          <p className="pm-csv-counts">
            <span className="pm-st pm-st-ready">{counts.ready} ready</span> <span className="pm-st pm-st-review">{counts.review} review needed</span>{" "}
            <span className="pm-st pm-st-duplicate">{counts.duplicate} duplicates</span> <span className="pm-st pm-st-error">{counts.error} errors</span>
          </p>
          <div className="pm-table-wrap">
            <table className="table pm-table pm-csv-table">
              <thead><tr><th /><th>#</th><th>Status</th><th>Card match</th><th>Grade</th><th className="pm-num">Price (USD)</th><th>Date</th><th>Source</th><th>Notes</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.row} className={`pm-csv-${r.status}`}>
                    <td><input type="checkbox" aria-label={`Import row ${r.row}`} checked={!!picked[r.row]} disabled={!selectable(r)}
                               onChange={(e) => setPicked({ ...picked, [r.row]: e.target.checked })} /></td>
                    <td>{r.row}</td>
                    <td><span className={`pm-st pm-st-${r.status}`}>{r.status === "review" ? "REVIEW NEEDED" : r.status.toUpperCase()}</span></td>
                    <td>
                      {r.status === "review" ? (
                        <select aria-label={`Card for row ${r.row}`} value={choice[r.row] ?? ""} onChange={(e) => {
                          const v = Number(e.target.value);
                          setChoice({ ...choice, [r.row]: v });
                          setPicked({ ...picked, [r.row]: !!v });
                        }}>
                          <option value="">Choose the exact card…</option>
                          {r.candidates.map((c) => <option key={c.card_id} value={c.card_id}>{c.label} ({Math.round(c.confidence * 100)}%)</option>)}
                        </select>
                      ) : r.card ? <>{r.card} {r.confidence !== null && <span className="muted">({Math.round(r.confidence * 100)}%)</span>}</> : DASH}
                    </td>
                    <td>{r.grade ?? DASH}</td>
                    <td className="pm-num">{r.price_usd !== undefined ? usd(r.price_usd) : DASH}
                      {r.currency && r.currency !== "USD" && r.fx_rate ? <span className="muted pm-small"> ({(r.price ?? 0) / 100} {r.currency} × {r.fx_rate})</span> : null}</td>
                    <td>{r.sale_date ? day(r.sale_date) : DASH}</td>
                    <td>{r.source ? sourceLabel(r.source) : DASH}</td>
                    <td className="pm-small">{[...r.errors, ...r.warnings, ...(r.status === "review" ? r.reasons.filter((x) => x.includes("not stated") || x.includes("below") || x.includes("almost")) : [])].join(" · ") || DASH}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <label className="pm-verify-check">
            <input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />
            I have checked these sales (they count as <strong>manual verified</strong>; unverified sales are stored but never priced)
          </label>
          <button type="button" className="btn btn-primary" disabled={busy || !Object.values(picked).some(Boolean)} onClick={() => void commit()}>
            Import {Object.values(picked).filter(Boolean).length} selected rows
          </button>
        </>
      )}
    </Card>
  );
}

/** REVIEW NEEDED: marketplace items the matcher wouldn't place automatically. */
function ReviewQueue() {
  const { data, reload } = useApi(() => pokemonApi.review());
  const [msg, setMsg] = useState<string | null>(null);
  if (!data?.items.length) return null;
  async function reject(l: Listing) {
    try {
      await pokemonApi.resolveReview(l.id, { reject: true });
      await reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Couldn't save.");
    }
  }
  return (
    <Card title={`Review needed (${data.items.length})`}>
      <p className="muted pm-small">Marketplace items whose exact card or grade couldn't be matched safely. Nothing from them is in the prices.</p>
      <ul className="pm-sales-mini">
        {data.items.map((l) => (
          <li key={l.id}>
            {l.url ? <a href={l.url} target="_blank" rel="noopener noreferrer">{l.title}</a> : <span>{l.title}</span>}
            <span className="muted">{l.kind === "sold" ? "SOLD" : "LISTING"}</span>
            <strong>{usd(l.price)}</strong>
            <span className="muted">{sourceLabel(l.provider)} · {l.match_reasons.join(" · ")}
              {" "}<button type="button" className="btn btn-sm btn-ghost" onClick={() => void reject(l)}>Reject</button></span>
          </li>
        ))}
      </ul>
      {msg && <p className="pm-down">{msg}</p>}
    </Card>
  );
}

/** Owner only: why portfolio holdings are (not) priced, provider failures, scanner confidence; retry without rescanning. */
function PortfolioDiagnostics() {
  const { data, reload } = useApi(() => request<Diagnostics>("/pokemon/portfolios/diagnostics"));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return null;
  async function retry() {
    setBusy(true); setMsg(null);
    try {
      const r = await request<{ cards_checked: number; results: Record<string, number>; holdings_revalued: number }>(
        "/pokemon/portfolios/diagnostics/retry", { method: "POST" }, 120_000);
      setMsg(`Checked ${r.cards_checked} cards (${Object.entries(r.results).map(([k, v]) => `${v} ${k}`).join(", ") || "nothing to fetch"}); re-valued ${r.holdings_revalued} holdings.`);
      await reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Retry failed.");
    } finally { setBusy(false); }
  }
  const s = data.scanner_7d;
  return (
    <Card title="Portfolio valuation diagnostics">
      <p className="muted pm-small">
        {data.holdings} holdings · {data.identified} linked to market data · {data.priced} priced ({data.priced_exact} exact, {data.priced_comparable} estimated) · {data.unpriced} unpriced ·
        {" "}{data.provider_failures_24h} provider failures (24h) · last refresh {data.last_refresh.at ? `${relativeTime(data.last_refresh.at)} (${data.last_refresh.status})` : DASH} ·
        {" "}scanner 7d: {s.matched}/{s.scans} matched, median score {s.median_top_score ?? DASH}, {s.low_confidence} low-confidence
      </p>
      <ul className="pm-sales-mini">
        {data.by_reason.map((r) => <li key={r.reason}><span>{r.label}</span><strong>{r.count}</strong></li>)}
      </ul>
      {data.unpriced_holdings.length > 0 && (
        <ul className="pm-sales-mini">
          {data.unpriced_holdings.map((h) => (
            <li key={h.holding_id}><span>#{h.holding_id} {h.card} · {h.grade}</span>
              <span className="muted">{h.note ?? h.reason}{h.prices_checked_at ? ` · prices checked ${day(h.prices_checked_at)}` : ""}</span></li>
          ))}
        </ul>
      )}
      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void retry()}>{busy ? "Fetching prices…" : "Retry valuations (no rescan)"}</button>
      {msg && <p className="muted pm-small">{msg}</p>}
    </Card>
  );
}
