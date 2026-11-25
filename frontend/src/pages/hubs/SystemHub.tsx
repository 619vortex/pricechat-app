import { lazy } from "react";
import { Link } from "react-router-dom";
import { api, request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { Hub, HubCard, HubCards, HubPanel, HubPanels, useHomeData, useHubFocus } from "../../components/hub";
import { Badge, relativeTime } from "../../components/ui";
import { bm } from "../brand/api";
import type { TgStatus } from "../TelegramTradingCard";
import type { VpnOverview } from "../VpnAdmin";

const SettingsPage = lazy(() => import("../Settings").then((m) => ({ default: m.SettingsPage })));
const ApiKeysPage = lazy(() => import("../ApiKeys").then((m) => ({ default: m.ApiKeysPage })));
const ActivityPage = lazy(() => import("../Activity").then((m) => ({ default: m.ActivityPage })));
const KalshiAdminPage = lazy(() => import("../KalshiAdmin").then((m) => ({ default: m.KalshiAdminPage })));

type Tone = "good" | "warn" | "bad" | "neutral";
interface Switch { name: string; state: string; tone: Tone; where: string; to: string }

/** System (owner-approved redesign, 2026-11-22): health, every stop and switch at a glance, and the settings, on one page. Was six
 *  tabs. Each switch keeps its control (and confirmation) where it already lives; this list only shows its state and goes there.
 *  News Desk's stop and market-data pause live in Price Chat Terminal, untouched. Settings (the main emergency stop) is open by default. */
export function SystemHub() {
  const focus = useHubFocus();
  const home = useHomeData();
  const s = home?.status;
  const x = useApi(api.xOperator, 60000);
  const tg = useApi(() => request<TgStatus>("/tradingos/admin/telegram-trading"), 60000);
  const brand = useApi(bm.overview, 60000);
  const vpn = useApi(() => request<VpnOverview>("/vpn/admin/overview"), 60000);

  const switches: Switch[] = [
    { name: "Emergency stop (X actions, approvals)", state: s ? (s.kill_switch ? "ON" : "off") : "…", tone: s?.kill_switch ? "bad" : "good", where: "Settings, below", to: "/settings" },
    { name: "News Desk emergency stop and market-data pause", state: "in Terminal", tone: "neutral", where: "Price Chat Terminal", to: "/terminal" },
    { name: "X: dry run / live", state: x.data ? (x.data.dry_run ? "DRY RUN" : "LIVE") : "…", tone: x.data && !x.data.dry_run ? "warn" : "neutral", where: "X page", to: "/x" },
    { name: "X: social actions", state: x.data ? (x.data.social_actions_active ? "active" : "paused") : "…", tone: x.data?.social_actions_active ? "good" : "warn", where: "X page", to: "/x" },
    { name: "Telegram trading: emergency stop", state: tg.data ? (tg.data.switches.kill ? "ON" : "off") : "…", tone: tg.data?.switches.kill ? "bad" : "good", where: "Telegram trading", to: "/telegram-trading" },
    { name: "Telegram trading: mainnet", state: tg.data ? (tg.data.switches.mainnet_execution ? "ON" : "OFF") : "…", tone: tg.data?.switches.mainnet_execution ? "warn" : "good", where: "Telegram trading", to: "/telegram-trading" },
    { name: "Brand Manager: emergency stop", state: brand.data ? (brand.data.automation.emergency_stop ? "ON" : "off") : "…", tone: brand.data?.automation.emergency_stop ? "bad" : "good", where: "Brand Manager", to: "/brand-manager" },
    { name: "Kiwi VPN: payments", state: vpn.data ? (vpn.data.settings.payments_enabled ? "ON" : "off") : "…", tone: vpn.data?.settings.payments_enabled ? "warn" : "neutral", where: "Kiwi VPN", to: "/vpn" },
  ];
  const failing = s?.failing_jobs ?? [];

  return (
    <Hub title="System" about="Health, every stop and switch at a glance, settings, keys and the activity log.">
      <HubCards>
        <HubCard title="Health" tone={failing.length ? "warn" : undefined}>
          {s ? (
            <ul className="hub-facts">
              <li><span>Background jobs</span>{failing.length ? <Badge tone="warn">{failing.length} with problems</Badge> : <Badge tone="good">all fine</Badge>}</li>
              <li><span>AI</span><Badge tone={s.ai ? "good" : "bad"}>{s.ai ? "working" : "offline"}</Badge></li>
              <li><span>Telegram</span><span>{s.telegram}</span></li>
              <li><span>X</span><span>{s.x}</span></li>
              <li><span>Signal scanner</span><span>{s.last_scan_at ? relativeTime(s.last_scan_at) : "hasn't run yet"}</span></li>
            </ul>
          ) : <p className="muted small">Loading…</p>}
          {failing.length > 0 && <p className="muted small">Having problems: {failing.join(", ")}</p>}
        </HubCard>
        <HubCard title="Kiwi VPN" to="/vpn">
          {vpn.data ? (
            <ul className="hub-facts">
              <li><span>VPN</span><Badge tone={vpn.data.settings.enabled ? "good" : "neutral"}>{vpn.data.settings.enabled ? "on" : "off"}</Badge></li>
              <li><span>Servers</span><b>{vpn.data.servers.length}</b></li>
              <li><span>Devices / members</span><b>{vpn.data.totals.devices} / {vpn.data.totals.members}</b></li>
              <li><span>Active (24 h)</span><b>{vpn.data.totals.active_24h}</b></li>
            </ul>
          ) : <p className="muted small">{vpn.failure ? "Couldn't load Kiwi VPN." : "Loading…"}</p>}
        </HubCard>
        <HubCard title="Intelligence (shadow)" to="/intelligence">
          <p className="muted small">What the AI models would decide next to News Desk and Trending, possible missed stories, the PC worker and AI spend. Shadow only: nothing is published by AI.</p>
        </HubCard>
        <HubCard title="Realm (test)" to="/realm" open="Play">
          <p className="muted small">Vortex Realm, the Warrior Kiwi game, to test it. Members play it in Passport.</p>
        </HubCard>
      </HubCards>

      <section className="hub-card" aria-label="Stops and switches">
        <div className="hub-card-head"><h2>Stops and switches</h2></div>
        <ul className="hub-switches">
          {switches.map((w) => (
            <li key={w.name}>
              <span>{w.name}</span>
              <Badge tone={w.tone}>{w.state}</Badge>
              <Link className="btn btn-sm btn-ghost" to={w.to}>{w.where}</Link>
            </li>
          ))}
        </ul>
      </section>

      <HubPanels focus={focus}>
        <HubPanel id="settings" title="Settings" summary="the emergency stop, background jobs and AI models" defaultOpen><SettingsPage /></HubPanel>
        <HubPanel id="apikeys" title="API keys" summary="keys for the services Price Chat connects to"><ApiKeysPage /></HubPanel>
        <HubPanel id="activity" title="Activity log" summary="everything Price Chat did, newest first"><ActivityPage /></HubPanel>
        <HubPanel id="kalshi" title="Kalshi bot" summary="balance, fee curve and markets (read-only)"><KalshiAdminPage /></HubPanel>
      </HubPanels>
    </Hub>
  );
}
