import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { Advanced, ApiErrorBanner, Badge, Card, Field, Loading, PageHeader, relativeTime } from "../components/ui";
import { ExchangeAutoCard } from "./ExchangeAutoCard";
import { SECTION_KEYS, SECTION_LABEL, displayTitle, sectionStates, type SectionKey } from "../website/websiteLogic";

const NETWORKS = ["telegram", "x", "youtube"] as const;
const NETWORK_NAME = { telegram: "Telegram", x: "X", youtube: "YouTube" };

/** Dashboard › Website: status, what visitors see, section switches and public links. Not a CMS. */
export function WebsitePage() {
  const { data, failure, reload } = useApi(api.websiteStatus, 30000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [links, setLinks] = useState<Record<string, string>>({});
  const [siteUrl, setSiteUrl] = useState("");
  const [frame, setFrame] = useState(0); // bump to reload the preview after a change

  useEffect(() => {
    if (!data) return;
    setLinks(data.saved_links);
    setSiteUrl(data.home.site_url ?? "");
  }, [data?.saved_links.telegram, data?.saved_links.x, data?.saved_links.youtube, data?.home.site_url]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(body: Parameters<typeof api.updateWebsiteSettings>[0]) {
    setBusy(true);
    try {
      await api.updateWebsiteSettings(body);
      setError(null);
      await reload();
      setFrame((f) => f + 1);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const states = data ? sectionStates(data.home) : null;
  const visible = states ? SECTION_KEYS.filter((k) => states[k].visible) : [];
  const live = data ? data.status === "connected" && data.visible_items > 0 : false;

  return (
    <>
      <PageHeader
        title="Website"
        subtitle="The public Price Chat website. Latest Content follows YouTube automatically: the newest public upload is always the featured item."
      />
      <ApiErrorBanner error={failure ?? error} />
      {!data || !states ? (
        <Loading />
      ) : (
        <div className="grid-2">
          <div className="stack">
            <Card title="Status">
              <dl className="kv">
                <dt>Website</dt>
                <dd>
                  <Badge tone={live ? "good" : "warn"}>{live ? "Showing real content" : "Waiting for content"}</Badge>{" "}
                  <span className="muted small">{visible.length} of {SECTION_KEYS.length} sections visible</span>
                </dd>
                <dt>Public address</dt>
                <dd>{data.home.site_url ?? <span className="muted">not set yet (local preview only; no canonical/share URL)</span>}</dd>
                <dt>Content source</dt>
                <dd>
                  YouTube{" "}
                  <Badge tone={data.status === "connected" ? "good" : data.status === "error" ? "bad" : "warn"}>
                    {data.status === "connected" ? "Connected" : data.status === "not_configured" ? "Not connected" : data.status}
                  </Badge>
                  {data.channel && <span className="muted small"> · {data.channel}</span>}
                </dd>
                <dt>Last content sync</dt>
                <dd>{data.last_sync_at ? relativeTime(data.last_sync_at) : "never"}</dd>
                <dt>Visible content</dt>
                <dd>{data.visible_items} item(s)</dd>
                {data.last_error && (
                  <>
                    <dt>Last problem</dt>
                    <dd className="tone-bad small">{data.last_error} (visitors still see the last synced content)</dd>
                  </>
                )}
              </dl>
            </Card>

            <Card title="Featured now">
              {data.featured ? (
                <ul className="feed">
                  <li>
                    {data.featured.thumbnail && <img src={data.featured.thumbnail} alt="" className="content-thumb" loading="lazy" />}
                    <div className="feed-body">
                      <span>{displayTitle(data.featured.title)}</span>
                      <span className="muted small">
                        {data.featured.type_label}
                        {data.featured.duration ? ` · ${data.featured.duration}` : ""} · {relativeTime(data.featured.published_at)}
                      </span>
                      {displayTitle(data.featured.title) !== data.featured.title && (
                        <span className="muted small">Original title (unchanged on YouTube and in Price Chat): {data.featured.title}</span>
                      )}
                    </div>
                  </li>
                </ul>
              ) : (
                <p className="muted">Nothing yet. The next public upload becomes the featured item automatically.</p>
              )}
              {data.showing.length > 1 && (
                <p className="muted small">
                  Then: {data.showing.slice(1).map((c) => displayTitle(c.title)).join(" · ")}
                </p>
              )}
              <p className="muted small">
                Place videos in What's It Worth? or Originals, or file them under Markets / Crypto / Money / Culture:{" "}
                <Link className="link" to="/content?tab=published">Content › Published</Link>
              </p>
            </Card>

            <Card title="Homepage sections">
              <p className="muted small">A section appears only when it is switched on <em>and</em> has real content. Nothing is ever filled with placeholders.</p>
              {SECTION_KEYS.map((k: SectionKey) => {
                const st = states[k];
                const locked = k === "price_board" && !data.price_board_source;
                return (
                  <div className="row" key={k}>
                    <span>
                      {SECTION_LABEL[k]}{" "}
                      {st.visible ? <Badge tone="good">visible</Badge> : <Badge tone="neutral">hidden</Badge>}
                      {!st.visible && <span className="muted small"> · {st.reason}</span>}
                    </span>
                    <label className="toggle" title={locked ? "Stays hidden until a market-data source is connected" : undefined}>
                      <input
                        type="checkbox"
                        aria-label={`Show ${SECTION_LABEL[k]}`}
                        checked={st.on}
                        disabled={busy}
                        onChange={(e) => void save({ sections: { [k]: e.target.checked } })}
                      />
                      <span />
                    </label>
                  </div>
                );
              })}
            </Card>

            <Card title="Public links">
              <form
                className="form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void save({ links, site_url: siteUrl });
                }}
              >
                {NETWORKS.map((n) => (
                  <Field
                    key={n}
                    label={NETWORK_NAME[n]}
                    hint={
                      n === "telegram"
                        ? "Your public group or channel link (https://t.me/...). Hidden until set."
                        : `Empty = the connected account${data.home.links[n] ? ` (${data.home.links[n]})` : " (none connected: hidden)"}`
                    }
                  >
                    <input
                      className="input"
                      type="url"
                      inputMode="url"
                      placeholder={n === "telegram" ? "https://t.me/..." : data.home.links[n] ?? "https://..."}
                      value={links[n] ?? ""}
                      onChange={(e) => setLinks({ ...links, [n]: e.target.value })}
                    />
                  </Field>
                ))}
                <Field label="Public website address" hint="Used for canonical and share links once the site is deployed. Leave empty until there is a real domain.">
                  <input className="input" type="url" inputMode="url" placeholder="https://..." value={siteUrl} onChange={(e) => setSiteUrl(e.target.value)} />
                </Field>
                <div>
                  <button className="btn btn-primary" type="submit" disabled={busy}>
                    Save
                  </button>
                </div>
              </form>
            </Card>
            <Advanced label="Pokémon exchange: simulated auto-trader"><ExchangeAutoCard /></Advanced>
          </div>

          <Card
            title="Preview"
            actions={
              <a className="link small" href="/" target="_blank" rel="noreferrer">
                Open full site ↗
              </a>
            }
          >
            <p className="muted small">
              Phone width, exactly as visitors see it. Also: <a className="link" href="/content" target="_blank" rel="noreferrer">all content page ↗</a>
            </p>
            <div style={{ display: "flex", justifyContent: "center" }}>
              <iframe
                key={frame}
                title="Website preview (phone width)"
                src="/"
                loading="lazy"
                style={{ width: 390, maxWidth: "100%", height: 760, border: "1px solid var(--border)", borderRadius: 12, background: "#050706" }}
              />
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
