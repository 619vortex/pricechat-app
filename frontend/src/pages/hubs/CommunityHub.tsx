import { lazy } from "react";
import { Link } from "react-router-dom";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { Fold, Hub, HubCard, HubCards, HubPanel, HubPanels, TodoList, useHomeData, useHubFocus, useTodo } from "../../components/hub";
import { Badge } from "../../components/ui";
import type { TgStatus } from "../TelegramTradingCard";

const RewardsAdminPage = lazy(() => import("../RewardsAdmin").then((m) => ({ default: m.RewardsAdminPage })));
const TradingBotAdminPage = lazy(() => import("../TradingBotAdmin").then((m) => ({ default: m.TradingBotAdminPage })));
const WorkersAdminPage = lazy(() => import("../WorkersAdmin").then((m) => ({ default: m.WorkersAdminPage })));
const InviteQuickPage = lazy(() => import("../InviteQuick").then((m) => ({ default: m.InviteQuickPage })));
const InviteBotPage = lazy(() => import("../InviteBot").then((m) => ({ default: m.InviteBotPage })));

const TG_TEXT: Record<string, [string, "good" | "warn" | "bad" | "neutral"]> = {
  connected: ["connected (read-only)", "good"], not_configured: ["not set up", "neutral"], disconnected: ["waiting for first sync", "warn"], error: ["connection problem", "bad"],
};

/** Community (owner-approved redesign, 2026-11-22): people and member products on one page. Was nine tabs. Requests from members are
 *  one list; rank rewards, VIP bot access, workers and invites are panels; Members, Telegram, Telegram trading and Pokémon stay full
 *  tools one click away. Telegram trading's mainnet switch and emergency stop stay on its own page, with their confirmations; their
 *  state is shown here, never folded. */
export function CommunityHub() {
  const focus = useHubFocus();
  const home = useHomeData();
  const requests = useTodo(["bot_access", "knight", "vip", "workers"]);
  const tg = useApi(() => request<TgStatus>("/tradingos/admin/telegram-trading"), 30000);
  const sw = tg.data?.switches;
  const [tgText, tgTone] = TG_TEXT[home?.status.telegram ?? ""] ?? [home?.status.telegram ?? "…", "neutral"];

  return (
    <Hub title="Community" about="Members and what they ask for, Telegram, invites, Telegram trading and the Pokémon market."
      actions={<Link className="btn btn-sm btn-primary" to="/invite-bot/send">Send invites</Link>}>
      <HubCards>
        <HubCard title="Requests from members" count={requests.reduce((n, t) => n + t.count, 0)}>
          <TodoList items={requests} empty="No requests waiting: VIP bot access, reward claims and worker reports are all handled." />
        </HubCard>
        <HubCard title="Members" to="/members" actions={<Link className="btn btn-sm btn-ghost" to="/members?tab=missions">Missions</Link>}>
          <ul className="hub-facts">
            <li><span>Members</span><b>{home?.numbers.members ?? "…"}</b></li>
            <li><span>New this week</span><b>{home?.numbers.new_members_7d ?? "…"}</b></li>
          </ul>
        </HubCard>
        <HubCard title="Telegram trading" to="/telegram-trading" tone={sw?.kill ? "bad" : sw?.mainnet_execution ? "warn" : undefined}>
          {sw ? (
            <ul className="hub-facts">
              <li><span>Emergency stop</span><Badge tone={sw.kill ? "bad" : "good"}>{sw.kill ? "ON" : "off"}</Badge></li>
              <li><span>Mainnet from Telegram</span><Badge tone={sw.mainnet_execution ? "warn" : "good"}>{sw.mainnet_execution ? "ON" : "OFF"}</Badge></li>
            </ul>
          ) : <p className="muted small">{tg.failure ? "Couldn't load Telegram trading." : "Loading…"}</p>}
          <p className="muted small">The switches are on the Telegram trading page, with their confirmations.</p>
        </HubCard>
        <HubCard title="Telegram community" to="/telegram">
          <ul className="hub-facts"><li><span>Chats</span><Badge tone={tgTone}>{tgText}</Badge></li></ul>
        </HubCard>
        <HubCard title="Pokémon market" to="/pokemon" open="Card prices"
          actions={<><Link className="btn btn-sm btn-ghost" to="/pokemon/marketplace">Marketplace queue</Link><Link className="btn btn-sm btn-ghost" to="/pokemon/vault">Vault queue</Link></>}>
          <p className="muted small">Card prices and charts, the marketplace and the vault.</p>
        </HubCard>
      </HubCards>

      <HubPanels focus={focus}>
        <HubPanel id="rewards" title="Rank rewards" summary="what each rank unlocks, and claims"><RewardsAdminPage /></HubPanel>
        <HubPanel id="vip" title="VIP bot access" summary="who asked to join, and the rules"><TradingBotAdminPage /></HubPanel>
        <HubPanel id="workers" title="Workers" summary="paid helpers and their reports"><WorkersAdminPage /></HubPanel>
        <HubPanel id="invites" title="Invites" summary="invite people to the Telegram group">
          <InviteQuickPage />
          <Fold title="How the invite bot works" summary="its settings and limits"><InviteBotPage /></Fold>
        </HubPanel>
      </HubPanels>
    </Hub>
  );
}

