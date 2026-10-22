import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { pokemonApi, type PCard } from "./api";
import { DASH, moveClass, pct, usd, yearSet } from "./format";
import "./pokemon.css";

/** The card is the star: its official image, or a quiet frame with the card's name when no image is known. */
export function CardThumb({ card, size = "sm" }: { card: PCard; size?: "sm" | "md" | "lg" }) {
  return card.image_url ? (
    <img className={`pm-thumb pm-thumb-${size}`} src={card.image_url} alt={`${card.name} (${card.set_name})`} loading="lazy" />
  ) : (
    <span className={`pm-thumb pm-thumb-${size} pm-thumb-empty`} role="img" aria-label={`${card.name}: no image yet`}
          title="No image of this card yet">
      <span>{card.name}</span>
      <em className="pm-noimg">No image</em>
    </span>
  );
}

/** Search the catalog: "Charizard 1st edition PSA 10", "Japanese Lugia Neo", "Pikachu Illustrator". */
export function SearchBox({ autoFocus = false }: { autoFocus?: boolean }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Awaited<ReturnType<typeof pokemonApi.search>> | null>(null);
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!q.trim()) {
      setRes(null);
      return;
    }
    const t = window.setTimeout(() => void pokemonApi.search(q).then((r) => { setRes(r); setOpen(true); }).catch(() => setRes(null)), 200);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const go = (id: number) => {
    setOpen(false);
    nav(`/pokemon/cards/${id}`);
  };
  return (
    <div className="pm-search" ref={box}>
      <input type="search" value={q} autoFocus={autoFocus} placeholder='Search cards: "Charizard 1st edition PSA 10", "Japanese Lugia Neo"…'
             aria-label="Search Pokémon cards" onChange={(e) => setQ(e.target.value)} onFocus={() => res && setOpen(true)}
             onKeyDown={(e) => { if (e.key === "Enter" && res?.results[0]) go(res.results[0].card.id); if (e.key === "Escape") setOpen(false); }} />
      {open && res && (
        <ul className="pm-search-results" role="listbox" aria-label="Search results">
          {res.results.length === 0 && <li className="pm-search-empty">No card matches “{q}”.</li>}
          {res.results.map((r) => (
            <li key={r.card.id} role="option" aria-selected={false}>
              <button type="button" onClick={() => go(r.card.id)}>
                <CardThumb card={r.card} />
                <span className="pm-search-name">
                  <strong>{r.card.name}</strong>
                  <span className="muted">{yearSet(r.card)} · {r.card.card_number ?? DASH} · {r.card.edition !== "—" ? r.card.edition : r.card.language}</span>
                </span>
                <span className="pm-search-price">{r.grade && <span className="muted">{r.grade} </span>}{usd(r.market_price)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Move({ v }: { v: number | null | undefined }) {
  return <span className={moveClass(v)}>{pct(v)}</span>;
}

/** Edition / language / variant chips: two Charizards never look alike. */
export function IdentityChips({ card }: { card: PCard }) {
  return (
    <span className="pm-chips">
      {card.edition !== "—" && <span className={`pm-tag pm-tag-${card.edition === "1st Edition" ? "first" : "edition"}`}>{card.edition}</span>}
      {card.language !== "English" && <span className="pm-tag pm-tag-lang">{card.language === "Japanese" ? "JP" : card.language}</span>}
      {card.variant && <span className="pm-tag">{card.variant}</span>}
    </span>
  );
}
