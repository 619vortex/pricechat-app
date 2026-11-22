import { lazy } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import { Hub, HubCard, HubCards, HubPanel, HubPanels, TodoList, useHomeData, useHubFocus, useTodo } from "../../components/hub";
import { Badge } from "../../components/ui";

const YouTubePage = lazy(() => import("../YouTube").then((m) => ({ default: m.YouTubePage })));
const WebsitePage = lazy(() => import("../WebsitePage").then((m) => ({ default: m.WebsitePage })));

const X_STATUS: Record<string, [string, "good" | "warn" | "bad" | "neutral"]> = {
  connected: ["connected", "good"], not_configured: ["not connected", "neutral"], disconnected: ["saved, not tested", "warn"],
  error: ["connection problem", "bad"], paused: ["reading paused", "warn"],
};

/** Publishing (owner-approved redesign, 2026-11-22): where things go out, on one page. Was four tabs (X, YouTube, Website, Signals).
 *  X and Signals stay full tools one click away (their switches live there, unchanged); YouTube and Website are panels here.
 *  News Desk publishing is in Price Chat Terminal, untouched. */
export function PublishingHub() {
  const focus = useHubFocus();
  const home = useHomeData();
  const signals = useTodo(["signals"]);
  const approvals = useTodo(["approvals"]);
  const op = useApi(api.xOperator, 60000);
  const x = op.data;
  const [xText, xTone] = X_STATUS[x?.x_status ?? home?.status.x ?? ""] ?? [x?.x_status ?? home?.status.x ?? "…", "neutral"];

  return (
    <Hub title="Publishing" about="Where things go out: X, signals, YouTube and the public website. News Desk posts are run from Price Chat Terminal.">
      <HubCards>
        <HubCard title="X / Twitter" to="/x" count={approvals.reduce((n, t) => n + t.count, 0)}
          actions={<><Link className="btn btn-sm btn-ghost" to="/x?tab=opportunities">Post ideas</Link><Link className="btn btn-sm btn-ghost" to="/approvals">Approvals</Link></>}>
          <ul className="hub-facts">
            <li><span>Account</span><Badge tone={xTone}>{xText}</Badge></li>
            {x && <li><span>Mode</span><Badge tone={x.dry_run ? "neutral" : "warn"}>{x.dry_run ? "DRY RUN" : "LIVE"}</Badge></li>}
            {x && <li><span>Social actions</span><Badge tone={x.social_actions_active ? "good" : "warn"}>{x.social_actions_active ? "active" : "paused"}</Badge></li>}
          </ul>
          <p className="muted small">The live / dry-run and social-action switches are on the X page, with their confirmations.</p>
        </HubCard>
        <HubCard title="Signals" to="/trading-os" open="Open signals" count={signals.reduce((n, t) => n + t.count, 0)}>
          <ul className="hub-facts">
            <li><span>Open now</span><b>{home?.numbers.open_signals ?? "…"}</b></li>
            <li><span>This week</span><b>{home?.numbers.signals_7d ?? "…"}</b></li>
          </ul>
          <TodoList items={signals} empty="No signals waiting for your OK." />
        </HubCard>
        <HubCard title="News Desk (Telegram + X)" to="/terminal" open="Open Price Chat Terminal">
          <p className="muted small">Breaking news, market posts, the emergency stop and market-data pause are run from Price Chat Terminal.</p>
        </HubCard>
      </HubCards>

      <HubPanels focus={focus}>
        <HubPanel id="youtube" title="YouTube" summary="your channel and new videos"><YouTubePage /></HubPanel>
        <HubPanel id="website" title="Website" summary="what the public website shows"><WebsitePage /></HubPanel>
      </HubPanels>
    </Hub>
  );
}
