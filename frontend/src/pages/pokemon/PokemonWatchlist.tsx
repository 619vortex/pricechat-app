import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiErrorBanner, Loading } from "../../components/ui";
import { marketApi, pokemonApi, type RankRow } from "./api";
import { GradeSelect, MarketShell, useWatch } from "./mx";
import { RankTable } from "./RankTable";

/** My watchlist: the starred cards as market rows at one grade (PSA 10 by default), same columns as the rankings. */
export function PokemonWatchlist() {
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Watchlist", null]]}>
      <WatchlistBody />
    </MarketShell>
  );
}

function WatchlistBody() {
  const [sp, setSp] = useSearchParams();
  const grade = sp.get("grade") ?? "PSA 10";
  const { ids } = useWatch();
  const [rows, setRows] = useState<RankRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const key = [...ids].sort((a, b) => a - b).join(",");
  useEffect(() => {
    let live = true;
    void pokemonApi.watchlist().then(async (w) => {
      const list = w.cards.map((c) => c.card.id);
      if (!list.length) return live && setRows([]);
      const m = await marketApi.market({ ids: list.join(","), grade, limit: 250, era: "all" });
      if (live) setRows(m.rows);
    }).catch((e) => live && setError(e));
    return () => { live = false; };
  }, [grade, key]);
  return (
    <>
      <header>
        <h1 className="mx-h1">★ My Watchlist</h1>
        <p className="mx-lede">Starred cards at one grade. Star any card in the rankings or on its page.</p>
      </header>
      <div className="mx-controls"><div className="mx-controls-left">
        <GradeSelect value={grade} onChange={(g) => { const p = new URLSearchParams(sp); if (g === "PSA 10") p.delete("grade"); else p.set("grade", g); setSp(p); }} />
      </div></div>
      <ApiErrorBanner error={error} />
      {!rows ? <Loading /> : rows.length === 0
        ? <p className="mx-banner"><b>No starred cards yet.</b> Press ☆ next to a card in the rankings or “Add to watchlist” on its page.</p>
        : <RankTable rows={rows} grade={grade} />}
    </>
  );
}
