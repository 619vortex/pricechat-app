import { lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AdminGate } from "./components/AdminGate";
import { Layout } from "./components/Layout";
import { Overview } from "./pages/Overview";
import { XPage } from "./pages/XPage";  // the overview uses it too

// every page but the overview is its own chunk, so the dashboard opens without downloading all of them (Layout's <Suspense>)
const AIBrain = lazy(() => import("./pages/AIBrain").then((m) => ({ default: m.AIBrain })));
const KnowledgePage = lazy(() => import("./pages/Knowledge").then((m) => ({ default: m.KnowledgePage })));
const ContentPage = lazy(() => import("./pages/Content").then((m) => ({ default: m.ContentPage })));
const ApprovalsPage = lazy(() => import("./pages/Approvals").then((m) => ({ default: m.ApprovalsPage })));
const WebsitePage = lazy(() => import("./pages/WebsitePage").then((m) => ({ default: m.WebsitePage })));
const CommunityPage = lazy(() => import("./pages/Community").then((m) => ({ default: m.CommunityPage })));
const TopicDetailPage = lazy(() => import("./pages/TopicDetail").then((m) => ({ default: m.TopicDetailPage })));
const Analytics = lazy(() => import("./pages/Analytics").then((m) => ({ default: m.Analytics })));
const ActivityPage = lazy(() => import("./pages/Activity").then((m) => ({ default: m.ActivityPage })));
const ApiKeysPage = lazy(() => import("./pages/ApiKeys").then((m) => ({ default: m.ApiKeysPage })));
const SettingsPage = lazy(() => import("./pages/Settings").then((m) => ({ default: m.SettingsPage })));
const YouTubePage = lazy(() => import("./pages/YouTube").then((m) => ({ default: m.YouTubePage })));
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
const InviteBotPage = lazy(() => import("./pages/InviteBot").then((m) => ({ default: m.InviteBotPage })));
const InviteQuickPage = lazy(() => import("./pages/InviteQuick").then((m) => ({ default: m.InviteQuickPage })));
const TradingBotAdminPage = lazy(() => import("./pages/TradingBotAdmin").then((m) => ({ default: m.TradingBotAdminPage })));
const AffiliateAdminPage = lazy(() => import("./pages/AffiliateAdmin").then((m) => ({ default: m.AffiliateAdminPage })));
const WorkersAdminPage = lazy(() => import("./pages/WorkersAdmin").then((m) => ({ default: m.WorkersAdminPage })));
const RewardsAdminPage = lazy(() => import("./pages/RewardsAdmin").then((m) => ({ default: m.RewardsAdminPage })));
const RealmAdminPage = lazy(() => import("./pages/RealmAdmin").then((m) => ({ default: m.RealmAdminPage })));
const VpnAdminPage = lazy(() => import("./pages/VpnAdmin").then((m) => ({ default: m.VpnAdminPage })));
const KalshiAdminPage = lazy(() => import("./pages/KalshiAdmin").then((m) => ({ default: m.KalshiAdminPage })));
const TelegramTradingPage = lazy(() => import("./pages/TelegramTradingPage").then((m) => ({ default: m.TelegramTradingPage })));
const TerminalPage = lazy(() => import("./pages/terminal/TerminalPage").then((m) => ({ default: m.TerminalPage })));
const TerminalNewsDesk = lazy(() => import("./pages/x/NewsDesk").then((m) => ({ default: m.NewsDesk })));
const TerminalTrending = lazy(() => import("./pages/x/TrendingTopics").then((m) => ({ default: m.TrendingTopics })));
const WorldMonitorPage = lazy(() => import("./pages/WorldMonitorPage").then((m) => ({ default: m.WorldMonitorPage })));
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
            <Route path="brain" element={<AIBrain />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="content" element={<ContentPage />} />
            <Route path="x" element={<XPage />} />
            <Route path="world-monitor" element={<WorldMonitorPage />} />
            <Route path="terminal" element={<TerminalPage />} />
            <Route path="terminal/newsdesk" element={<TerminalNewsDesk />} />
            <Route path="terminal/trending" element={<TerminalTrending />} />
            <Route path="brand-manager/*" element={<BrandManagerPage />} />
            <Route path="short-market/*" element={<ShortMarketPage />} />
            <Route path="approvals" element={<ApprovalsPage />} />
            <Route path="website" element={<WebsitePage />} />
            <Route path="youtube" element={<YouTubePage />} />
            <Route path="members" element={<MembersPage />} />
            <Route path="trading-bot" element={<TradingBotAdminPage />} />
            <Route path="affiliate" element={<AffiliateAdminPage />} />
            <Route path="workers" element={<WorkersAdminPage />} />
            <Route path="rank-rewards" element={<RewardsAdminPage />} />
            <Route path="realm" element={<RealmAdminPage />} />
            <Route path="vpn" element={<VpnAdminPage />} />
            <Route path="kalshi" element={<KalshiAdminPage />} />
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
            <Route path="invite-bot" element={<InviteBotPage />} />
            <Route path="invite-bot/send" element={<InviteQuickPage />} />
            <Route path="telegram/topics/:id" element={<TopicDetailPage />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="activity" element={<ActivityPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="api-keys" element={<ApiKeysPage />} />
            <Route path="*" element={<Overview />} />
          </Route>
        </Routes>
      </AdminGate>
    </BrowserRouter>
  );
}
