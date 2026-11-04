import { StrictMode, Suspense, lazy, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

// After a deploy, an open tab may ask for a page chunk that no longer exists: reload once to get the new build
// (guarded, so a genuinely broken chunk can't cause a reload loop).
window.addEventListener("vite:preloadError", (event) => {
  try {
    const last = Number(sessionStorage.getItem("pc-chunk-reload") ?? 0);
    if (Date.now() - last < 60_000) return;
    sessionStorage.setItem("pc-chunk-reload", String(Date.now()));
  } catch {
    /* storage blocked: still reload once */
  }
  event.preventDefault();
  location.reload();
});
import "./styles.css";

// The public website is loaded on demand so its fonts and styles never weigh on the dashboard.
const Website = lazy(() => import("./website/Website").then((m) => ({ default: m.Website })));
const ContentArchive = lazy(() => import("./website/Website").then((m) => ({ default: m.ContentArchive })));
const community = () => import("./website/community");
const JoinPage = lazy(() => community().then((m) => ({ default: m.JoinPage })));
const passport = () => import("./website/passport");
const ProfilePage = lazy(() => passport().then((m) => ({ default: m.ProfilePage })));
const PublicProfilePage = lazy(() => passport().then((m) => ({ default: m.PublicProfilePage })));
const LeaderboardPage = lazy(() => community().then((m) => ({ default: m.LeaderboardPage })));
const GiveawayPage = lazy(() => import("./website/giveaway").then((m) => ({ default: m.GiveawayPage })));
const LegalPage = lazy(() => community().then((m) => ({ default: m.LegalPage })));
const PublicMarket = lazy(() => import("./website/market").then((m) => ({ default: m.PublicMarket })));
const TradingBotPage = lazy(() => import("./website/tradingbot").then((m) => ({ default: m.TradingBotPage })));
const KiwiBrainPage = lazy(() => import("./website/kiwibrain").then((m) => ({ default: m.KiwiBrainPage })));
const LunchPage = lazy(() => import("./website/arbitrage").then((m) => ({ default: m.LunchPage })));
const FundingPage = lazy(() => import("./website/arbitrage").then((m) => ({ default: m.FundingPage })));
const KalshiPage = lazy(() => import("./website/kalshi").then((m) => ({ default: m.KalshiPage })));
const LearnPage = lazy(() => import("./website/rewards").then((m) => ({ default: m.LearnPage })));
const VpnPage = lazy(() => import("./website/vpn").then((m) => ({ default: m.VpnPage })));
const VpnDesktopPage = lazy(() => import("./website/vpnDesktop").then((m) => ({ default: m.VpnDesktopPage })));
const VpnDevicesPage = lazy(() => import("./website/vpn").then((m) => ({ default: m.VpnDevicesPage })));
const VpnReviewerPage = lazy(() => import("./website/vpn").then((m) => ({ default: m.VpnReviewerPage })));

const page = (el: React.ReactNode) => <Suspense fallback={null}>{el}</Suspense>;

/** The public site: the website at "/", the Kiwi Passport at "/passport". The old /website/... addresses still work. */
function PublicSite() {
  return (
    <BrowserRouter>
      <Routes>
        <Route index element={page(<Website />)} />
        <Route path="content" element={page(<ContentArchive />)} />
        <Route path="passport" element={page(<ProfilePage />)} />
        <Route path="join" element={page(<JoinPage />)} />
        <Route path="join/:code" element={page(<JoinPage />)} />
        <Route path="leaderboard" element={page(<LeaderboardPage />)} />
        <Route path="giveaway" element={page(<GiveawayPage />)} />
        <Route path="earn" element={page(<TradingBotPage />)} />
        <Route path="earn/brain" element={page(<KiwiBrainPage />)} />
        <Route path="earn/lunch" element={page(<LunchPage />)} />
        <Route path="earn/funding" element={page(<FundingPage />)} />
        <Route path="earn/kalshi" element={page(<KalshiPage />)} />
        <Route path="earn/arbitrage" element={<Navigate to="/earn/lunch" replace />} />
        <Route path="earn/committee" element={<Navigate to="/earn" replace />} />
        <Route path="trading-bot" element={page(<TradingBotPage />)} />
        <Route path="learn" element={page(<LearnPage />)} />
        <Route path="vpn" element={page(<VpnPage />)} />
        <Route path="vpn/privacy" element={page(<LegalPage kind="vpnprivacy" />)} />
        <Route path="vpn/desktop" element={page(<VpnDesktopPage />)} />
        <Route path="vpn/devices" element={page(<VpnDevicesPage />)} />
        <Route path="vpn/reviewer" element={page(<VpnReviewerPage />)} />
        <Route path="arena" element={<Navigate to="/passport#realm" replace />} />
        <Route path="realm" element={<Navigate to="/passport#realm" replace />} />
        <Route path="pokemon/*" element={page(<PublicMarket />)} />
        <Route path="u/:username" element={page(<PublicProfilePage />)} />
        {/* earlier addresses (links already shared, OAuth return pages) */}
        <Route path="website/preview" element={page(<Website />)} />
        <Route path="website/content" element={page(<ContentArchive />)} />
        <Route path="website/join" element={page(<JoinPage />)} />
        <Route path="website/join/:code" element={page(<JoinPage />)} />
        <Route path="website/profile" element={page(<ProfilePage />)} />
        <Route path="website/leaderboard" element={page(<LeaderboardPage />)} />
        <Route path="website/u/:username" element={page(<PublicProfilePage />)} />
        {(["privacy", "terms", "disclaimer", "contact"] as const).map((k) => (
          <Route key={k} path={`website/${k}`} element={page(<LegalPage kind={k} />)} />
        ))}
        {/* the dashboard used to live at the root: send those addresses to /admin */}
        {ADMIN_PAGES.map((p) => (
          <Route key={p} path={p === "telegram" ? "telegram/*" : p} element={<ToAdmin />} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

const ADMIN_PAGES = ["brain", "knowledge", "x", "approvals", "website", "youtube", "members", "telegram", "analytics", "activity", "settings", "api-keys"];

function ToAdmin() {
  const { pathname, search } = useLocation();
  // a full page load: the dashboard is a separate app (chosen when the page loads), behind the owner login
  useEffect(() => location.replace(`/admin${pathname}${search}`), [pathname, search]);
  return null;
}

/** The internal dashboard (its own chunk: public visitors never download it). */
const AdminApp = lazy(() => import("./AdminApp").then((m) => ({ default: m.AdminApp })));

const isAdmin = location.pathname === "/admin" || location.pathname.startsWith("/admin/");

createRoot(document.getElementById("root")!).render(<StrictMode>{isAdmin ? page(<AdminApp />) : <PublicSite />}</StrictMode>);
