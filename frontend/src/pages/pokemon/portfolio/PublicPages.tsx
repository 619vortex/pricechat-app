import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { RankKiwi } from "../../../website/RankKiwi";
import { openLogin } from "../../../website/emailLogin";
import { DASH, num, usd } from "../format";
import { usePageHead } from "../mx";
import { portfolioApi, type Comment, type ExploreRow, type Holding, type PublicPortfolio } from "./api";
import { CompositionBars, GradeBadge, HoldingSheet, HoldingTile, PortfolioFrame, ValueCell, errorText, useMember } from "./shared";

/** A collector's public portfolio. Private ones are "not found" to everyone else (enforced by the API). */
export function PublicPortfolioPage() {
  const { username = "" } = useParams();
  const member = useMember();
  const [p, setP] = useState<PublicPortfolio | null | undefined>(undefined);
  const [sort, setSort] = useState("value");
  const [open, setOpen] = useState<Holding | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    void portfolioApi.publicPortfolio(username, sort).then(setP).catch((e) => { setP(null); setErr(errorText(e)); });
  }, [username, sort]);
  useEffect(() => { if (member !== undefined) load(); }, [load, member]);
  usePageHead(p ? { title: `${p.username}'s Pokémon Card Collection · Price Chat`,
                    description: `${p.username}'s Pokémon portfolio on Price Chat: ${num(p.cards)} cards, ${num(p.unique_cards)} unique, ${num(p.graded)} graded.${p.bio ? ` ${p.bio}` : ""}` } : null);
  const crumbs: [string, string | null][] = [["Markets", "/pokemon"], ["Collectors", "/pokemon/portfolios"], [username, null]];
  if (p === undefined) return <PortfolioFrame crumbs={crumbs}><p className="pf-muted">Loading…</p></PortfolioFrame>;
  if (p === null) return <PortfolioFrame crumbs={crumbs}><section className="pf-empty"><h1>Portfolio not available</h1><p>{err}</p><Link className="pf-btn" to="/pokemon/portfolios">Explore collectors</Link></section></PortfolioFrame>;
  return (
    <PortfolioFrame crumbs={crumbs}>
      <section className="pf-profile">
        <div className="pf-profile-kiwi"><RankKiwi art={p.rank.art as never} rankKey={p.rank.key} size={96} /></div>
        <div className="pf-profile-main">
          <h1>{p.username}{p.owner && p.visibility !== "public" && <span className="pf-tag">Private: only you see this</span>}</h1>
          <p className="pf-muted">{p.rank.name} Kiwi</p>
          {p.bio && <p className="pf-bio">{p.bio}</p>}
          <dl className="pf-stats">
            {p.show_value && <div><dt>Est. collection value</dt><dd>{p.value !== null ? usd(p.value) : DASH}</dd></div>}
            <div><dt>Cards</dt><dd>{num(p.cards)}</dd></div>
            <div><dt>Unique</dt><dd>{num(p.unique_cards)}</dd></div>
            <div><dt>Graded</dt><dd>{num(p.graded)}</dd></div>
            <div><dt>Raw</dt><dd>{num(p.raw)}</dd></div>
          </dl>
          <p className="pf-fine">{p.value_note}</p>
          {!p.owner && <ReportLink target="portfolio" id={0} username={p.username} />}
        </div>
      </section>
      {(p.featured.length > 0 || p.top.length > 0) && (
        <section className="pf-box">
          <h2>{p.featured.length ? "Featured cards" : "Most valuable cards"}</h2>
          <div className="pf-gallery pf-gallery-feature">{(p.featured.length ? p.featured : p.top).map((h) => <HoldingTile key={h.id} h={h} onOpen={setOpen} showChanges={false} />)}</div>
        </section>
      )}
      {p.composition && (
        <section className="pf-box"><h2>Collection breakdown</h2>
          <div className="pf-comps"><CompositionBars title="By set" groups={p.composition.set} total={p.value} /><CompositionBars title="By grade" groups={p.composition.grade} total={p.value} /></div>
        </section>
      )}
      <section className="pf-box">
        <div className="pf-boxhead">
          <h2>Collection <small>{p.total} entries</small></h2>
          <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
            {p.show_value && <option value="value">Highest value</option>}<option value="recent">Recently added</option><option value="name">Name</option><option value="set">Set</option>
          </select>
        </div>
        <div className="pf-gallery">{p.holdings.map((h) => <HoldingTile key={h.id} h={h} onOpen={setOpen} showChanges={false} />)}</div>
      </section>
      <Comments username={p.username} owner={p.owner} enabled={p.comments_enabled} signedIn={!!member} />
      {open && <HoldingSheet h={open} owner={p.owner} onClose={() => setOpen(null)} onChanged={load} />}
    </PortfolioFrame>
  );
}

function ReportLink({ target, id, username }: { target: "comment" | "portfolio"; id: number; username?: string }) {
  const [sent, setSent] = useState(false);
  if (sent) return <span className="pf-fine">Reported. Thank you.</span>;
  return (
    <button type="button" className="pf-link pf-fine" onClick={() => {
      const reason = window.prompt(`What's wrong with this ${target}?`);
      if (!reason) return;
      void portfolioApi.report(target, id, username ? `${username}: ${reason}` : reason).then(() => setSent(true)).catch((e) => window.alert(errorText(e)));
    }}>Report</button>
  );
}

function Comments({ username, owner, enabled, signedIn }: { username: string; owner: boolean; enabled: boolean; signedIn: boolean }) {
  const [list, setList] = useState<Comment[] | null>(null);
  const [body, setBody] = useState("");
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { void portfolioApi.comments(username).then((r) => setList(r.comments)).catch(() => setList([])); }, [username]);
  useEffect(load, [load]);
  const post = async () => {
    setErr(null);
    try {
      await portfolioApi.comment(username, body, replyTo);
      setBody("");
      setReplyTo(null);
      load();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  const one = (c: Comment, reply = false) => (
    <li key={c.id} className={reply ? "pf-reply" : ""}>
      <p className="pf-comment-head"><b>{c.author}</b> <time>{new Date(c.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</time></p>
      <p className={c.deleted ? "pf-muted" : ""}>{c.deleted ? "Comment deleted." : c.body}</p>
      <div className="pf-comment-actions">
        {!reply && signedIn && enabled && !c.deleted && <button type="button" className="pf-link" onClick={() => setReplyTo(c.id)}>Reply</button>}
        {c.can_delete && <button type="button" className="pf-link" onClick={() => {
          if (window.confirm("Delete this comment?")) void portfolioApi.deleteComment(c.id).then(load).catch((e) => setErr(errorText(e)));
        }}>Delete</button>}
        {signedIn && !c.deleted && !c.can_delete && <ReportLink target="comment" id={c.id} />}
      </div>
      {c.replies && c.replies.length > 0 && <ul>{c.replies.map((r) => one(r, true))}</ul>}
    </li>
  );
  return (
    <section className="pf-box pf-comments">
      <h2>Comments</h2>
      {!enabled ? <p className="pf-muted">{owner ? "You've turned comments off." : "Comments are off for this portfolio."}</p> : signedIn ? (
        <form onSubmit={(e) => { e.preventDefault(); if (body.trim()) void post(); }}>
          {replyTo && <p className="pf-fine">Replying · <button type="button" className="pf-link" onClick={() => setReplyTo(null)}>cancel</button></p>}
          <textarea value={body} maxLength={1000} rows={2} placeholder="Say something about this collection (no links)" onChange={(e) => setBody(e.target.value)} />
          <button type="submit" className="pf-btn" disabled={!body.trim()}>Post</button>
          {err && <p className="pf-error">{err}</p>}
        </form>
      ) : <p><button type="button" className="pf-link" onClick={() => openLogin()}>Sign in</button> to comment.</p>}
      <ul className="pf-commentlist">{list?.map((c) => one(c))}</ul>
      {list && list.length === 0 && <p className="pf-muted">No comments yet.</p>}
    </section>
  );
}

const TABS: [string, string][] = [["top", "Top Portfolios"], ["cards", "Most Cards"], ["valuable", "Most Valuable Cards"], ["recent", "Recently Updated"], ["featured", "Featured Collectors"]];

export function ExplorePage() {
  const [tab, setTab] = useState("top");
  const [data, setData] = useState<{ rows: unknown[]; note?: string } | null>(null);
  const [open, setOpen] = useState<Holding | null>(null);
  usePageHead({ title: "Explore Pokémon Card Collectors · Price Chat", description: "Public Pokémon card portfolios from the Price Chat community: top collections, most cards, most valuable cards and featured collectors." });
  useEffect(() => {
    setData(null);
    void portfolioApi.explore(tab).then(setData).catch(() => setData({ rows: [] }));
  }, [tab]);
  return (
    <PortfolioFrame crumbs={[["Markets", "/pokemon"], ["Explore Collectors", null]]}>
      <header className="pf-explore-head">
        <h1>Explore Collectors</h1>
        <p className="pf-muted">Public Pokémon portfolios from the Price Chat community. Values are estimates of self-reported collections; Price Chat hasn't verified ownership.</p>
      </header>
      <div className="pf-seg pf-seg-wrap" role="tablist">
        {TABS.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? "is-on" : ""} onClick={() => setTab(k)}>{label}</button>)}
      </div>
      {!data ? <p className="pf-muted">Loading…</p> : data.rows.length === 0 ? (
        <p className="pf-muted pf-pad">{tab === "featured" ? "No featured collectors yet." : "No public portfolios here yet. Make yours public from My Portfolio › Sharing & privacy."}</p>
      ) : tab === "valuable" ? (
        <div className="pf-gallery">{(data.rows as (Holding & { owner: string })[]).map((h) => (
          <div key={h.id} className="pf-tile-wrap"><HoldingTile h={h} onOpen={setOpen} showChanges={false} /><Link className="pf-owner-link" to={`/pokemon/portfolio/u/${h.owner}`}>@{h.owner}</Link></div>
        ))}</div>
      ) : (
        <ol className="pf-board">
          {(data.rows as ExploreRow[]).map((r) => (
            <li key={r.username}>
              <Link to={`/pokemon/portfolio/u/${r.username}`}>
                <span className="pf-board-pos">{r.position}</span>
                <RankKiwi art={r.rank.art as never} rankKey={r.rank.key} size={40} />
                <span className="pf-board-name"><b>{r.username}</b><small>{r.rank.name} Kiwi{r.bio ? ` · ${r.bio}` : ""}</small></span>
                <span className="pf-board-val">{r.value !== null ? <b>{usd(r.value, true)}</b> : <small>value hidden</small>}<small>{num(r.cards)} cards</small></span>
              </Link>
            </li>
          ))}
        </ol>
      )}
      {open && <HoldingSheet h={open} owner={false} onClose={() => setOpen(null)} />}
    </PortfolioFrame>
  );
}

export { GradeBadge, ValueCell };
