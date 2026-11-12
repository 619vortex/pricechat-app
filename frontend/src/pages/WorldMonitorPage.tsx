import { PageHeader } from "../components/ui";
import { ViralEngine } from "./x/ViralEngine";
import { WorldMonitor } from "./x/WorldMonitor";

/** Admin → Social media → World Monitor: what the News Desk is reading right now, how it judged each story, and what it will
 *  post next on Telegram and X, then the Viral Engine's hooks and results. Its own page in the sidebar (owner, 2026-11-12:
 *  "why there is no world monitor in admin panel": it was the sixth card down X › News Desk). */
export function WorldMonitorPage() {
  return (
    <>
      <PageHeader title="WORLD MONITOR" subtitle="Every story the News Desk is reading, how it judged it, and exactly what it will post next on Telegram and X." />
      <WorldMonitor />
      <ViralEngine />
    </>
  );
}
