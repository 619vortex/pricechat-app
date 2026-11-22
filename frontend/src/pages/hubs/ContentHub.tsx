import { lazy } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../../api/useApi";
import { Hub, HubCard, HubCards, HubPanel, HubPanels, TodoList, useHubFocus, useTodo } from "../../components/hub";
import { Badge } from "../../components/ui";
import { bm } from "../brand/api";
import { sm } from "../shortmarket/api";

const ContentPage = lazy(() => import("../Content").then((m) => ({ default: m.ContentPage })));
const AIBrain = lazy(() => import("../AIBrain").then((m) => ({ default: m.AIBrain })));
const KnowledgePage = lazy(() => import("../Knowledge").then((m) => ({ default: m.KnowledgePage })));

/** Content (owner-approved redesign, 2026-11-22): one page for everything being made. Was five tabs (Ideas & drafts, Brand Manager,
 *  Short Market, AI Brain, Knowledge). Brand Manager and Short Market stay full tools, one click away; the rest are panels here. */
export function ContentHub() {
  const focus = useHubFocus();
  const waiting = useTodo(["drafts", "memories"]);
  const brand = useApi(bm.overview, 60000);
  const shorts = useApi(sm.overview, 60000);
  const a = brand.data?.automation;
  const st = brand.data?.stats;
  const c = shorts.data?.counts;

  return (
    <Hub title="Content" about="Everything being made: ideas and drafts, Brand Manager posts, Short Market videos, and what the AI knows and how it talks.">
      <HubCards>
        <HubCard title="Waiting for review" count={waiting.reduce((n, t) => n + t.count, 0)}>
          <TodoList items={waiting} empty="No drafts or memories waiting." />
        </HubCard>
        <HubCard title="Brand Manager" to="/brand-manager" tone={a?.emergency_stop ? "bad" : undefined}
          actions={<>
            <Link className="btn btn-sm btn-ghost" to="/brand-manager/studio">Studio</Link>
            <Link className="btn btn-sm btn-ghost" to="/brand-manager/calendar">Calendar</Link>
            <Link className="btn btn-sm btn-ghost" to="/brand-manager/trends">Trends</Link>
          </>}>
          {a ? (
            <ul className="hub-facts">
              <li><span>Automation</span>{a.emergency_stop ? <Badge tone="bad">emergency stop on</Badge> : <Badge tone={a.running ? "good" : "neutral"}>{a.status}</Badge>}</li>
              <li><span>Made today</span><b>{st?.created_today ?? 0}</b></li>
              <li><span>Published today</span><b>{st?.published_today ?? 0}</b></li>
              <li><span>Coming up</span><b>{st?.upcoming ?? 0}</b></li>
            </ul>
          ) : <p className="muted small">{brand.failure ? "Couldn't load Brand Manager." : "Loading…"}</p>}
        </HubCard>
        <HubCard title="Short Market" to="/short-market">
          {c ? (
            <ul className="hub-facts">
              <li><span>PC worker</span><Badge tone={shorts.data?.worker.online ? "good" : "warn"}>{shorts.data?.worker.online ? "online" : "offline"}</Badge></li>
              <li><span>Queued / rendering</span><b>{c.queued} / {c.rendering}</b></li>
              <li><span>Ready</span><b>{c.ready}</b></li>
              {c.failed > 0 && <li><span>Failed</span><Badge tone="bad">{c.failed}</Badge></li>}
            </ul>
          ) : <p className="muted small">{shorts.failure ? "Couldn't load Short Market." : "Loading…"}</p>}
        </HubCard>
      </HubCards>

      <HubPanels focus={focus}>
        <HubPanel id="ideas" title="Ideas & drafts" summary="post ideas and drafts to review" defaultOpen><ContentPage /></HubPanel>
        <HubPanel id="ai" title="AI voice" summary="goals, brand profile, test chat"><AIBrain /></HubPanel>
        <HubPanel id="knowledge" title="Knowledge" summary="what the AI knows about Price Chat"><KnowledgePage /></HubPanel>
      </HubPanels>
    </Hub>
  );
}
