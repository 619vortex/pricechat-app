import { useCallback, useEffect, useMemo, useState } from "react";
import { memberApi, type Rank } from "./members";
import { RankKiwi } from "./RankKiwi";
import "./identity.css";

/** Kiwi Passport 2.0 (PASSPORT2_MODE): the public identity. One <PassportCard> draws both the public page (/u/<name>) and the
 *  editor's live preview, so what a member sees while choosing is exactly what others will see. Badges, titles and the community
 *  score never carry money value and never count towards the Kiwi rank. */

export type PBadge = {
  key: string; name: string; description: string; family: string; family_name: string; color: string; tier: "bronze" | "silver" | "gold" | "mythic";
  title: string | null; earned: boolean; awarded_at: string | null; serial: number | null; featured_slot: number | null;
  rarity: { holders: number; members: number; share: number; label: string }; limit?: { cap: number; until: string; claimed: number };
};
export type PredSummary = { calls: number; decided: number; right: number; streak: number; best_streak: number; last: (boolean | null)[]; accuracy: number | null };
export type PublicV2 = {
  v: 2; username: string; joined_at: string; rank: Rank; roles?: string[]; display_name: string | null; accent: string; title: string | null; bio: string | null;
  featured: PBadge[]; badge_count: number; sections: Record<string, boolean>; card: string; collection?: PBadge[];
  community?: { score: number; level: string }; predictions?: PredSummary; streak?: { current: number; best: number }; platforms?: string[]; invite?: string;
};
export type CommunityScore = { score: number; level: { name: string; floor: number; next_at: number | null; next_name: string | null }; window_days: number;
  parts: { key: string; label: string; count: number; points: number }[]; penalty: number };
export type Identity = {
  public_profile: boolean; invited: boolean; title_badge: string | null; bio: string; accent: string; accents: string[]; accent_unlocked: boolean;
  featured: string[]; sections: Record<string, boolean>; ref_link: boolean; titles: { badge: string; title: string }[]; collection: PBadge[];
  community: CommunityScore; preview: PublicV2; share_url: string; invite: string | null;
  rules: { bio_max: number; featured_slots: number; score_window_days: number };
};

export const ACCENT: Record<string, string> = { green: "#00e58f", gold: "#f5c542", ice: "#7fd6ff", ember: "#ff7a45", violet: "#b98cff" };
const ACCENT_NAME: Record<string, string> = { green: "Kiwi green", gold: "Gold", ice: "Ice", ember: "Ember", violet: "Violet" };
const SECTION_LABEL: Record<string, string> = { community: "Community score", predictions: "Predictions record", streak: "Daily streak",
  platforms: "Verified platforms", badges: "Badge case" };
const PLATFORM_LABEL: Record<string, string> = { telegram: "Telegram", x: "X", google: "Google", youtube: "YouTube", wallet: "Wallet" };
const FAMILY_GLYPH: Record<string, string> = { founders: "★", community: "💬", predictor: "🎯", streak: "🔥", collector: "🃏", recruiter: "🤝", verified: "✓", honours: "♛" };

export function usePassport2(): [Identity | null | false, (next?: Identity) => Promise<void>] {
  const [data, setData] = useState<Identity | null | false>(null);
  const load = useCallback(async (next?: Identity) => {
    if (next) { setData(next); return; }
    try { setData(await memberApi<Identity>("/members/passport2")); } catch { setData(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return [data, load];
}

const since = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
const rarityText = (b: PBadge) => (b.serial && b.limit ? `No. ${String(b.serial).padStart(3, "0")} of ${b.limit.cap}` : b.rarity.label);

/** A collectible badge: the Kiwi on its family colour, ringed in its tier's metal. */
export function KiwiBadge({ b, size = 56, locked = false }: { b: PBadge; size?: number; locked?: boolean }) {
  return (
    <span className={`kb kb-${b.tier}${locked ? " is-locked" : ""}`} style={{ "--kb-s": `${size}px`, "--kb-fam": b.color } as React.CSSProperties}
          role="img" aria-label={`${b.name}${locked ? " (not earned yet)" : ""}`}>
      <img src="/brand/kiwi.webp" alt="" loading="lazy" />
      <i aria-hidden="true">{FAMILY_GLYPH[b.family] ?? "★"}</i>
    </span>
  );
}

/** The public Passport. `p` is the public page payload; in the editor, the live preview built from the member's unsaved choices. */
export function PassportCard({ p, onShare, actions }: { p: PublicV2; onShare?: () => void; actions?: React.ReactNode }) {
  const sec = p.sections;
  const name = p.display_name || p.username;
  const stats: [string, string][] = [];
  if (sec.community && p.community) stats.push([p.community.score.toLocaleString("en-US"), `Community · ${p.community.level}`]);
  if (sec.predictions && p.predictions) stats.push([p.predictions.accuracy != null ? `${Math.round(p.predictions.accuracy * 100)}%` : String(p.predictions.calls),
    p.predictions.accuracy != null ? `Calls right · ${p.predictions.decided}` : "Predictions calls"]);
  if (sec.streak && p.streak) stats.push([String(p.streak.current), `Day streak · best ${p.streak.best}`]);
  stats.push([String(p.badge_count), p.badge_count === 1 ? "Badge" : "Badges"]);
  return (
    <article className="pc2" style={{ "--pc2-accent": ACCENT[p.accent] ?? ACCENT.green } as React.CSSProperties} aria-label={`${name}'s Kiwi Passport`}>
      <header className="pc2-top">
        <div className="pc2-medal">
          <RankKiwi art={p.rank.art} rankKey={p.rank.key} size={132} label={`${p.rank.name} Kiwi`} worker={(p.roles ?? []).includes("WORKER")} />
          <span className="pc2-lvl">Rank {p.rank.level}</span>
        </div>
        <div className="pc2-id">
          {p.title && <p className="pc2-title">{p.title}</p>}
          <h1 className="pc2-name">{name}</h1>
          <p className="pc2-rank">{p.rank.name}{p.display_name ? <span> · @{p.username}</span> : null}<span> · since {since(p.joined_at)}</span></p>
          {p.bio && <p className="pc2-bio">{p.bio}</p>}
        </div>
      </header>

      {p.featured.length > 0 && (
        <ul className="pc2-feat" aria-label="Featured badges">
          {p.featured.map((b) => (
            <li key={b.key}><KiwiBadge b={b} size={52} /><div><b>{b.name}</b><span>{rarityText(b)}</span></div></li>
          ))}
        </ul>
      )}

      <dl className="pc2-stats" style={{ "--n": stats.length } as React.CSSProperties}>
        {stats.map(([v, l]) => <div key={l}><dt>{l}</dt><dd>{v}</dd></div>)}
      </dl>

      {((sec.predictions && p.predictions && p.predictions.last.length > 0) || (sec.platforms && p.platforms && p.platforms.length > 0)) && (
        <div className="pc2-row">
          {sec.predictions && p.predictions && p.predictions.last.length > 0 && (
            <section className="pc2-box">
              <h2>Latest Bitcoin calls</h2>
              <div className="pc2-dots" aria-label={`${p.predictions.last.filter(Boolean).length} right of the last ${p.predictions.last.length}`}>
                {p.predictions.last.map((r, i) => <i key={i} className={r === true ? "r" : r === false ? "w" : ""} />)}
              </div>
            </section>
          )}
          {sec.platforms && p.platforms && p.platforms.length > 0 && (
            <section className="pc2-box">
              <h2>Verified on</h2>
              <ul className="pc2-plats">{p.platforms.map((x) => <li key={x}>{PLATFORM_LABEL[x] ?? x}</li>)}</ul>
            </section>
          )}
        </div>
      )}

      {sec.badges && p.collection && p.collection.length > 0 && (
        <section className="pc2-box">
          <h2>Badge case</h2>
          <ul className="pc2-case">{p.collection.map((b) => <li key={b.key} title={`${b.name} · ${rarityText(b)}`}><KiwiBadge b={b} size={40} /></li>)}</ul>
        </section>
      )}

      {(onShare || actions || p.invite) && (
        <footer className="pc2-actions">
          {onShare && <button type="button" className="site-button pc2-btn" onClick={onShare}>Share profile</button>}
          {p.invite && <a className="site-textbutton" href={p.invite}>Join Price Chat with {name}'s invite</a>}
          {actions}
        </footer>
      )}
    </article>
  );
}

/** What the public page looks like with the member's unsaved choices (the server's preview carries every section). */
function livePreview(d: Identity, draft: Draft): PublicV2 {
  const byKey = new Map(d.collection.map((b) => [b.key, b]));
  const title = d.titles.find((t) => t.badge === draft.title)?.title ?? null;
  return {
    ...d.preview, accent: draft.accent, title, bio: draft.bio.trim() || null, sections: draft.sections,
    featured: draft.featured.map((k) => byKey.get(k)).filter((b): b is PBadge => !!b),
    invite: draft.ref_link ? d.preview.invite : undefined,
  };
}

type Draft = { title: string; bio: string; accent: string; featured: string[]; sections: Record<string, boolean>; ref_link: boolean };
const draftOf = (d: Identity): Draft => ({ title: d.title_badge ?? "", bio: d.bio, accent: d.accent, featured: d.featured, sections: d.sections, ref_link: d.ref_link });

/** The one-time invitation (Passport home): a live preview and two answers. Never shown again once answered. */
export function PassportInvite({ data, onAnswered }: { data: Identity; onAnswered: (d: Identity) => void }) {
  const [busy, setBusy] = useState(false);
  if (data.invited || data.public_profile) return null;
  async function answer(action: "accept" | "later") {
    setBusy(true);
    try { onAnswered(await memberApi<Identity>("/members/passport2/invite", { method: "POST", body: JSON.stringify({ action }) })); } finally { setBusy(false); }
  }
  return (
    <section className="pc2-invite" aria-labelledby="pc2-invite-h">
      <div className="pc2-invite-copy">
        <p className="site-label">New · Kiwi Passport</p>
        <h2 id="pc2-invite-h" className="site-display">Show the community who you are</h2>
        <p>Turn on your public Passport: your rank, the badges you pick and your community score, at pricechat.live/u/{data.preview.username}. You choose
          every section, and you can switch it off at any time.</p>
        <div className="pc2-invite-row">
          <button type="button" className="site-button" disabled={busy} onClick={() => void answer("accept")}>Make my Passport public</button>
          <button type="button" className="site-textbutton" disabled={busy} onClick={() => void answer("later")}>Not now</button>
        </div>
      </div>
      <div className="pc2-invite-card" aria-hidden="true"><PassportCard p={{ ...data.preview, sections: { ...data.preview.sections, badges: false } }} /></div>
    </section>
  );
}

/** Passport › Profile: everything on one screen, the choices on one side and the public page updating as you choose on the other. */
export function ProfileTab({ data, reload }: { data: Identity; reload: (d?: Identity) => Promise<void> }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(data));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setDraft(draftOf(data)), [data]);
  const preview = useMemo(() => livePreview(data, draft), [data, draft]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(data));
  const earned = data.collection.filter((b) => b.earned);
  const set = (patch: Partial<Draft>) => { setDraft((d) => ({ ...d, ...patch })); setMsg(null); };

  async function save(extra: Record<string, unknown> = {}) {
    setSaving(true); setMsg(null);
    try {
      const body = { title_badge: draft.title, bio: draft.bio, featured: draft.featured, sections: draft.sections, ref_link: draft.ref_link,
        ...(draft.accent !== data.accent ? { accent: draft.accent } : {}), ...extra };
      await reload(await memberApi<Identity>("/members/passport2", { method: "PATCH", body: JSON.stringify(body) }));
      setMsg({ ok: true, text: "Saved ✓" });
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : "That didn't save. Try again." }); } finally { setSaving(false); }
  }
  function toggleFeatured(k: string) {
    if (draft.featured.includes(k)) set({ featured: draft.featured.filter((x) => x !== k) });
    else if (draft.featured.length < data.rules.featured_slots) set({ featured: [...draft.featured, k] });
    else setMsg({ ok: false, text: `Feature up to ${data.rules.featured_slots} badges. Take one out first.` });
  }

  return (
    <div className="pc2-studio" data-testid="passport-profile">
      <form className="pc2-panel" onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label="Your public Passport">
        <div className="pc2-public">
          <div><b>{data.public_profile ? "Your Passport is public" : "Your Passport is private"}</b>
            <span>{data.public_profile ? `Anyone can see it at pricechat.live${data.share_url}` : "Only you can see this preview."}</span></div>
          <button type="button" className={data.public_profile ? "site-textbutton" : "site-button"} disabled={saving}
                  onClick={() => void save({ public_profile: !data.public_profile })}>{data.public_profile ? "Make private" : "Make public"}</button>
        </div>

        <fieldset className="pc2-field">
          <legend>Frame colour</legend>
          <div className="pc2-swatches">
            {data.accents.map((a) => (
              <button key={a} type="button" className="pc2-sw" style={{ "--c": ACCENT[a] } as React.CSSProperties} aria-label={ACCENT_NAME[a] ?? a}
                      aria-pressed={draft.accent === a} disabled={!data.accent_unlocked && a !== data.accent} onClick={() => set({ accent: a })} />
            ))}
          </div>
          {!data.accent_unlocked && <p className="pc2-hint">Frames unlock at Kiwi rank (rank 3).</p>}
        </fieldset>

        <div className="pc2-field">
          <label htmlFor="pc2-title">Title under your name</label>
          <select id="pc2-title" value={draft.title} onChange={(e) => set({ title: e.target.value })}>
            <option value="">No title</option>
            {data.titles.map((t) => <option key={t.badge} value={t.badge}>{t.title}</option>)}
          </select>
          <p className="pc2-hint">Every title comes from a badge you've earned.</p>
        </div>

        <div className="pc2-field">
          <label htmlFor="pc2-bio">One line about you</label>
          <input id="pc2-bio" type="text" value={draft.bio} maxLength={data.rules.bio_max} placeholder="What you trade, collect or talk about"
                 onChange={(e) => set({ bio: e.target.value })} />
          <p className="pc2-hint">{draft.bio.length}/{data.rules.bio_max} · no links or @handles</p>
        </div>

        <fieldset className="pc2-field">
          <legend>Featured badges <span>{draft.featured.length}/{data.rules.featured_slots}</span></legend>
          <div className="pc2-pick">
            {earned.map((b) => (
              <button key={b.key} type="button" className="pc2-chip" aria-pressed={draft.featured.includes(b.key)} onClick={() => toggleFeatured(b.key)}>
                <KiwiBadge b={b} size={24} />{b.name}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="pc2-field">
          <legend>Show on my public page</legend>
          {Object.keys(SECTION_LABEL).map((k) => (
            <label key={k} className="pc2-toggle"><span>{SECTION_LABEL[k]}</span>
              <input type="checkbox" checked={!!draft.sections[k]} onChange={(e) => set({ sections: { ...draft.sections, [k]: e.target.checked } })} /></label>
          ))}
          <label className="pc2-toggle"><span>My invite link <small>friends who join with it count towards your Recruiter badges</small></span>
            <input type="checkbox" checked={draft.ref_link} onChange={(e) => set({ ref_link: e.target.checked })} /></label>
          <p className="pc2-hint">Linked accounts show as a tick only: never a handle, email or wallet address.</p>
        </fieldset>

        <div className="pc2-save">
          <button type="submit" className="site-button" disabled={!dirty || saving}>{saving ? "Saving…" : "Save"}</button>
          {dirty && !saving && <button type="button" className="site-textbutton" onClick={() => { setDraft(draftOf(data)); setMsg(null); }}>Undo changes</button>}
          {msg && <p className={msg.ok ? "pc2-ok" : "site-error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
        </div>
      </form>

      <div className="pc2-stage">
        <p className="pc2-url">pricechat.live<b>{data.share_url}</b>{!data.public_profile && <em>private</em>}</p>
        <PassportCard p={preview} />
        <ShareCards data={data} />
      </div>

      <Community score={data.community} />
      <Collection items={data.collection} />
    </div>
  );
}

function ShareCards({ data }: { data: Identity }) {
  const [copied, setCopied] = useState(false);
  const url = `https://pricechat.live${data.share_url}`;
  const v = useMemo(() => Date.now().toString(36), [data]);  // a fresh card after every save
  async function copy() {
    try { await navigator.clipboard.writeText(url); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { /* the link is shown */ }
  }
  const text = encodeURIComponent("My Kiwi Passport on Price Chat 🥝");
  return (
    <section className="pc2-share" aria-labelledby="pc2-share-h">
      <h2 id="pc2-share-h">Your share card</h2>
      <img src={`/api/members/passport2/card.png?fmt=link&v=${v}`} alt="Your Kiwi Passport share card" loading="lazy" />
      {data.public_profile ? (
        <div className="pc2-share-row">
          <button type="button" className="site-button" onClick={() => void copy()}>{copied ? "Link copied ✓" : "Copy link"}</button>
          <a className="site-textbutton" href={`https://x.com/intent/post?text=${text}&url=${encodeURIComponent(url)}`} target="_blank" rel="noopener noreferrer">Post on X</a>
          <a className="site-textbutton" href={`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${text}`} target="_blank" rel="noopener noreferrer">Send on Telegram</a>
          <a className="site-textbutton" href="/api/members/passport2/card.png?fmt=story" download="kiwi-passport-story.png">Story size</a>
        </div>
      ) : <p className="pc2-hint">Make your Passport public to share it. In Telegram, send /passport to @CryptoPriceChatBot for this card any time.</p>}
      <p className="pc2-hint">Saved changes show on the card. Link previews on X and Telegram update within about ten minutes.</p>
    </section>
  );
}

function Community({ score }: { score: CommunityScore }) {
  const lvl = score.level;
  const pct = lvl.next_at ? Math.min(1, (score.score - lvl.floor) / (lvl.next_at - lvl.floor)) : 1;
  const max = Math.max(1, ...score.parts.map((x) => x.points));
  return (
    <section className="pc2-community" aria-labelledby="pc2-cs-h">
      <div className="pc2-cs-head">
        <div><p className="site-label">Community score · last {score.window_days} days</p>
          <h2 id="pc2-cs-h"><b>{score.score.toLocaleString("en-US")}</b> {lvl.name}</h2></div>
        <div className="pc2-cs-next">
          <span className="pc2-bar"><i style={{ width: `${Math.round(pct * 100)}%` }} /></span>
          <small>{lvl.next_at ? `${(lvl.next_at - score.score).toLocaleString("en-US")} to ${lvl.next_name}` : "Top level"}</small>
        </div>
      </div>
      <ul className="pc2-parts">
        {score.parts.map((x) => (
          <li key={x.key}><span>{x.label}</span><span className="pc2-part-bar"><i style={{ width: `${(x.points / max) * 100}%` }} /></span>
            <b>{x.points.toLocaleString("en-US")}</b></li>
        ))}
      </ul>
      <p className="pc2-hint">Only genuine participation counts: taking part in the Telegram, helping others, missions, friends who stay and Predictions calls.
        Packs, purchases, deposits, trading and sign-ins never count{score.penalty ? `, and an upheld moderation flag costs ${score.penalty}` : ""}. The score
        doesn't change your XP, rank or rewards.</p>
    </section>
  );
}

function Collection({ items }: { items: PBadge[] }) {
  const families = useMemo(() => [...new Map(items.map((b) => [b.family, b.family_name])).entries()], [items]);
  const [fam, setFam] = useState("all");
  const [sel, setSel] = useState<string | null>(null);
  const shown = items.filter((b) => fam === "all" || b.family === fam);
  const pick = items.find((b) => b.key === sel) ?? null;
  const got = items.filter((b) => b.earned).length;
  return (
    <section className="pc2-collection" aria-labelledby="pc2-col-h">
      <div className="pc2-col-head">
        <div><p className="site-label">Collection</p><h2 id="pc2-col-h"><b>{got}</b> of {items.length} Kiwi badges</h2></div>
        <div className="pc2-fams" role="group" aria-label="Badge family">
          {[["all", "All"], ...families].map(([k, l]) => (
            <button key={k} type="button" className="pc2-chip" aria-pressed={fam === k} onClick={() => setFam(k)}>{l}</button>
          ))}
        </div>
      </div>
      <ul className="pc2-grid">
        {shown.map((b) => (
          <li key={b.key}>
            <button type="button" className={`pc2-tile${b.earned ? "" : " is-locked"}`} aria-pressed={sel === b.key} onClick={() => setSel(sel === b.key ? null : b.key)}>
              <KiwiBadge b={b} size={60} locked={!b.earned} />
              <b>{b.name}</b>
              <span className={`pc2-rar is-${b.rarity.label.toLowerCase()}`}>{rarityText(b)}</span>
            </button>
          </li>
        ))}
      </ul>
      {pick && (
        <div className="pc2-detail" role="status">
          <KiwiBadge b={pick} size={72} locked={!pick.earned} />
          <div>
            <p className="site-label" style={{ color: pick.color }}>{pick.family_name} · {pick.tier}</p>
            <h3>{pick.name}</h3>
            <p>{pick.description}</p>
            <p className="pc2-hint">{pick.earned ? `Earned ${new Date(pick.awarded_at!).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}` : "Not earned yet"}
              {" · "}{pick.rarity.holders.toLocaleString("en-US")} of {pick.rarity.members.toLocaleString("en-US")} members hold it ({pick.rarity.label})
              {pick.limit ? ` · ${pick.limit.claimed} of ${pick.limit.cap} numbered, closes ${new Date(pick.limit.until).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })}` : ""}
              {pick.title ? ` · gives the title "${pick.serial ? pick.name : pick.title}"` : ""}</p>
          </div>
        </div>
      )}
    </section>
  );
}
