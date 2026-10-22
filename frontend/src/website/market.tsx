import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { MarketHome } from "../pages/pokemon/MarketHome";
import { GradesPage, MoversPage, RecentPage, SetsPage, TrendingPage, YearsPage } from "../pages/pokemon/ListPages";
import { PokemonCardPage } from "../pages/pokemon/PokemonCardPage";
import { PublicMarketProvider } from "../pages/pokemon/mx";
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
const wait = (el: React.ReactNode) => <Suspense fallback={<p className="pf-muted" style={{ padding: 24 }}>Loading…</p>}>{el}</Suspense>;

/** The Pokémon Market on the public website (pricechat.live/pokemon): the same price pages as the dashboard's, read-only,
 * inside the website's header and footer, with the PRICE CHAT VAULT storefront as a tab (/pokemon/vault). The simulated
 * trading (Paper Exchange, Play-USDT QuickTrade, the simulated P2P marketplace) is retired: its addresses lead here. */
export function PublicMarket() {
  const home = useStored<HomeData>("/api/website/home");
  useHead({ title: "Pokémon Market · Price Chat", description: "Vintage Pokémon card prices, market caps and verified sales, and the Price Chat Vault of vintage Japanese cards.", path: "/pokemon",
            siteUrl: home?.site_url ?? null, image: site.shareImage });
  return (
    <div className="site site-market">
      <SiteHeader home={home} base="/" solid />
      <main className="site-market-main">
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
            <Route path="cards/:id" element={<PokemonCardPage />} />
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
      </main>
      <SiteFooter home={home} />
    </div>
  );
}
