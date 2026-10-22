import { Suspense, lazy, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { MarketHome } from "../pages/pokemon/MarketHome";
import { GradesPage, MoversPage, RecentPage, SetsPage, TrendingPage, YearsPage } from "../pages/pokemon/ListPages";
import { MarketShell, PageHeadProvider, PublicMarketProvider, Skeleton, type PageHead } from "../pages/pokemon/mx";
import { useHead } from "./useHead";
import { SiteFooter, SiteHeader, useStored } from "./Website";
import { site } from "./websiteData";
import type { HomeData } from "./websiteLogic";

const portfolio = () => import("../pages/pokemon/portfolio");
const PortfolioPage = lazy(() => portfolio().then((m) => ({ default: m.PortfolioPage })));
const AddCardsPage = lazy(() => portfolio().then((m) => ({ default: m.AddCardsPage })));
const PublicPortfolioPage = lazy(() => portfolio().then((m) => ({ default: m.PublicPortfolioPage })));
const ExplorePage = lazy(() => portfolio().then((m) => ({ default: m.ExplorePage })));
const PhoneLinkPage = lazy(() => portfolio().then((m) => ({ default: m.PhoneLinkPage })));
const vault = () => import("../pages/pokemon/vault");
const VaultStore = lazy(() => vault().then((m) => ({ default: m.VaultStore })));
const VaultItemPage = lazy(() => vault().then((m) => ({ default: m.VaultItemPage })));
const VaultRequestsPage = lazy(() => vault().then((m) => ({ default: m.VaultRequestsPage })));
const marketplace = () => import("../pages/pokemon/marketplace");
const WalletPage = lazy(() => marketplace().then((m) => ({ default: m.WalletPage })));
// the card page brings the chart library: the market home and lists don't download it until a card is opened
const PokemonCardPage = lazy(() => import("../pages/pokemon/PokemonCardPage").then((m) => ({ default: m.PokemonCardPage })));
const cardPage = <Suspense fallback={<MarketShell><Skeleton rows={6} boxes /></MarketShell>}><PokemonCardPage /></Suspense>;
const wait = (el: React.ReactNode) => <Suspense fallback={<p className="pf-muted" style={{ padding: 24 }}>Loading…</p>}>{el}</Suspense>;

/** The Pokémon Market on the public website (pricechat.live/pokemon): the same price pages as the dashboard's, read-only,
 * inside the website's header and footer, with the PRICE CHAT VAULT storefront as a tab (/pokemon/vault). The simulated
 * trading (Paper Exchange, Play-USDT QuickTrade, the simulated P2P marketplace) is retired: its addresses lead here. */
export function PublicMarket() {
  const home = useStored<HomeData>("/api/website/home");
  // each page may name itself (a card, a list, a Vault card); the canonical URL is always the page's own path
  const [page, setPage] = useState<PageHead | null>(null);
  const { pathname } = useLocation();
  useHead({ title: page?.title ?? "Pokémon Market · Price Chat",
            description: page?.description ?? "Vintage Pokémon card prices, market caps and verified sales, and the Price Chat Vault of vintage Japanese cards.",
            path: pathname, siteUrl: home?.site_url ?? null, image: page?.image || site.shareImage });
  return (
    <div className="site site-market">
      <SiteHeader home={home} base="/" solid />
      <main className="site-market-main">
        <PageHeadProvider value={setPage}>
        <PublicMarketProvider value={true}>
          <Routes>
            <Route index element={<MarketHome />} />
            <Route path="trending" element={<TrendingPage />} />
            <Route path="gainers" element={<MoversPage direction="gainers" />} />
            <Route path="losers" element={<MoversPage direction="losers" />} />
            <Route path="recent" element={<RecentPage />} />
            <Route path="years" element={<YearsPage />} />
            <Route path="sets" element={<SetsPage />} />
            <Route path="grades" element={<GradesPage />} />
            <Route path="cards/:id" element={cardPage} />
            <Route path="vault" element={wait(<VaultStore />)} />
            <Route path="vault/requests" element={wait(<VaultRequestsPage />)} />
            <Route path="vault/:id" element={wait(<VaultItemPage />)} />
            <Route path="portfolio" element={wait(<PortfolioPage />)} />
            <Route path="portfolio/add" element={wait(<AddCardsPage />)} />
            <Route path="portfolio/phone/:token" element={wait(<PhoneLinkPage />)} />
            <Route path="portfolio/u/:username" element={wait(<PublicPortfolioPage />)} />
            <Route path="portfolios" element={wait(<ExplorePage />)} />
            <Route path="wallet" element={wait(<WalletPage />)} />
            <Route path="marketplace/wallet" element={<Navigate to="/pokemon/wallet" replace />} />
            <Route path="*" element={<Navigate to="/pokemon" replace />} />
          </Routes>
        </PublicMarketProvider>
        </PageHeadProvider>
      </main>
      <SiteFooter home={home} />
    </div>
  );
}
