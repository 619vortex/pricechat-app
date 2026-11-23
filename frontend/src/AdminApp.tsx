import { lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AdminGate } from "./components/AdminGate";
import { Layout } from "./components/Layout";
import { Overview } from "./pages/Overview";
import { XPage } from "./pages/XPage";  // the overview uses it too

// every page but the overview is its own chunk, so the dashboard opens without downloading all of them (Layout's <Suspense>)
// Admin redesign (owner, 2026-11-22): a section is one page (a hub); old addresses open it with the right panel open
const ContentHub = lazy(() => import("./pages/hubs/ContentHub").then((m) => ({ default: m.ContentHub })));
const CommunityPage = lazy(() => import("./pages/Community").then((m) => ({ default: m.CommunityPage })));
const TopicDetailPage = lazy(() => import("./pages/TopicDetail").then((m) => ({ default: m.TopicDetailPage })));
const CommunityHub = lazy(() => import("./pages/hubs/CommunityHub").then((m) => ({ default: m.CommunityHub })));
const SystemHub = lazy(() => import("./pages/hubs/SystemHub").then((m) => ({ default: m.SystemHub })));
const PublishingHub = lazy(() => import("./pages/hubs/PublishingHub").then((m) => ({ default: m.PublishingHub })));
const MembersPage = lazy(() => import("./pages/Members").then((m) => ({ default: m.MembersPage })));
const MarketHome = lazy(() => import("./pages/pokemon/MarketHome").then((m) => ({ default: m.MarketHome })));
const lp = () => import("./pages/pokemon/ListPages");
const GradesPage = lazy(() => lp().then((m) => ({ default: m.GradesPage })));
const MoversPage = lazy(() => lp().then((m) => ({ default: m.MoversPage })));
const QualityPage = lazy(() => lp().then((m) => ({ default: m.QualityPage })));
const RecentPage = lazy(() => lp().then((m) => ({ default: m.RecentPage })));
const SetsPage = lazy(() => lp().then((m) => ({ default: m.SetsPage })));
const TrendingPage = lazy(() => lp().then((m) => ({ default: m.TrendingPage })));
const YearsPage = lazy(() => lp().then((m) => ({ default: m.YearsPage })));
const PokemonCardPage = lazy(() => import("./pages/pokemon/PokemonCardPage").then((m) => ({ default: m.PokemonCardPage })));
const PokemonWatchlist = lazy(() => import("./pages/pokemon/PokemonWatchlist").then((m) => ({ default: m.PokemonWatchlist })));
const PokemonSources = lazy(() => import("./pages/pokemon/PokemonSources").then((m) => ({ default: m.PokemonSources })));
const MarketplaceAdmin = lazy(() => import("./pages/pokemon/MarketplaceAdmin").then((m) => ({ default: m.MarketplaceAdmin })));
const VaultAdmin = lazy(() => import("./pages/pokemon/VaultAdmin").then((m) => ({ default: m.VaultAdmin })));
const BrandManagerPage = lazy(() => import("./pages/brand/BrandManager").then((m) => ({ default: m.BrandManagerPage })));
const ShortMarketPage = lazy(() => import("./pages/shortmarket/ShortMarket").then((m) => ({ default: m.ShortMarketPage })));
const RealmAdminPage = lazy(() => import("./pages/RealmAdmin").then((m) => ({ default: m.RealmAdminPage })));
const VpnAdminPage = lazy(() => import("./pages/VpnAdmin").then((m) => ({ default: m.VpnAdminPage })));
const TelegramTradingPage = lazy(() => import("./pages/TelegramTradingPage").then((m) => ({ default: m.TelegramTradingPage })));
const TerminalPage = lazy(() => import("./pages/terminal/TerminalPage").then((m) => ({ default: m.TerminalPage })));
const XIntelPage = lazy(() => import("./pages/terminal/XIntelPage").then((m) => ({ default: m.XIntelPage })));
const TerminalNewsDesk = lazy(() => import("./pages/x/NewsDesk").then((m) => ({ default: m.NewsDesk })));
const TerminalTrending = lazy(() => import("./pages/x/TrendingTopics").then((m) => ({ default: m.TrendingTopics })));
const WorldMonitorPage = lazy(() => import("./pages/WorldMonitorPage").then((m) => ({ default: m.WorldMonitorPage })));
const GummyBakedPage = lazy(() => import("./pages/sideprojects/GummyBakedPage").then((m) => ({ default: m.GummyBakedPage })));
const TradingOSPage = lazy(() => import("./pages/TradingOS").then((m) => ({ default: m.TradingOSPage })));
const pf = () => import("./pages/pokemon/portfolio");
const PortfolioPage = lazy(() => pf().then((m) => ({ default: m.PortfolioPage })));
const AddCardsPage = lazy(() => pf().then((m) => ({ default: m.AddCardsPage })));
const PublicPortfolioPage = lazy(() => pf().then((m) => ({ default: m.PublicPortfolioPage })));
const ExplorePage = lazy(() => pf().then((m) => ({ default: m.ExplorePage })));
const PhoneLinkPage = lazy(() => pf().then((m) => ({ default: m.PhoneLinkPage })));

/** The internal dashboard, under /admin, behind the owner login (the server protects every admin API either way). */
export function AdminApp() {
  return (
    <BrowserRouter basename="/admin">
      <AdminGate>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="content" element={<ContentHub />} />
            <Route path="brain" element={<ContentHub />} />
            <Route path="knowledge" element={<ContentHub />} />
            <Route path="x" element={<XPage />} />
            <Route path="world-monitor" element={<WorldMonitorPage />} />
            <Route path="terminal" element={<TerminalPage />} />
            <Route path="terminal/x" element={<XIntelPage />} />
            <Route path="terminal/newsdesk" element={<TerminalNewsDesk />} />
            <Route path="terminal/trending" element={<TerminalTrending />} />
            <Route path="brand-manager/*" element={<BrandManagerPage />} />
            <Route path="short-market/*" element={<ShortMarketPage />} />
            <Route path="approvals" element={<Overview />} />
            <Route path="publishing" element={<PublishingHub />} />
            <Route path="website" element={<PublishingHub />} />
            <Route path="youtube" element={<PublishingHub />} />
            <Route path="members" element={<MembersPage />} />
            <Route path="community" element={<CommunityHub />} />
            <Route path="trading-bot" element={<CommunityHub />} />
            <Route path="affiliate" element={<Overview />} />
            <Route path="workers" element={<CommunityHub />} />
            <Route path="rank-rewards" element={<CommunityHub />} />
            <Route path="realm" element={<RealmAdminPage />} />
            <Route path="vpn" element={<VpnAdminPage />} />
            <Route path="side-projects/gummy-baked" element={<GummyBakedPage />} />
            <Route path="kalshi" element={<SystemHub />} />
            <Route path="arena" element={<Navigate to="/realm" replace />} />
            <Route path="trading-os" element={<TradingOSPage />} />
            <Route path="pokemon" element={<MarketHome />} />
            <Route path="pokemon/trending" element={<TrendingPage />} />
            <Route path="pokemon/gainers" element={<MoversPage direction="gainers" />} />
            <Route path="pokemon/losers" element={<MoversPage direction="losers" />} />
            <Route path="pokemon/recent" element={<RecentPage />} />
            <Route path="pokemon/years" element={<YearsPage />} />
            <Route path="pokemon/sets" element={<SetsPage />} />
            <Route path="pokemon/grades" element={<GradesPage />} />
            <Route path="pokemon/quality" element={<QualityPage />} />
            <Route path="pokemon/cards/:id" element={<PokemonCardPage />} />
            <Route path="pokemon/watchlist" element={<PokemonWatchlist />} />
            <Route path="pokemon/sources" element={<PokemonSources />} />
            <Route path="pokemon/marketplace" element={<MarketplaceAdmin />} />
            <Route path="pokemon/vault" element={<VaultAdmin />} />
            <Route path="pokemon/portfolio" element={<PortfolioPage />} />
            <Route path="pokemon/portfolio/add" element={<AddCardsPage />} />
            <Route path="pokemon/portfolio/u/:username" element={<PublicPortfolioPage />} />
            <Route path="pokemon/portfolios" element={<ExplorePage />} />
            <Route path="pokemon/portfolio/phone/:token" element={<PhoneLinkPage />} />
            <Route path="telegram-trading" element={<TelegramTradingPage />} />
            <Route path="telegram" element={<CommunityPage />} />
            <Route path="invite-bot" element={<CommunityHub />} />
            <Route path="invite-bot/send" element={<CommunityHub />} />
            <Route path="telegram/topics/:id" element={<TopicDetailPage />} />
            <Route path="analytics" element={<Overview />} />
            <Route path="activity" element={<SystemHub />} />
            <Route path="system" element={<SystemHub />} />
            <Route path="settings" element={<SystemHub />} />
            <Route path="api-keys" element={<SystemHub />} />
            <Route path="*" element={<Overview />} />
          </Route>
        </Routes>
      </AdminGate>
    </BrowserRouter>
  );
}
