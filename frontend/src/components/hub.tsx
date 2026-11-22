import { createContext, Suspense, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import type { AdminHome, TodoItem } from "../api/home";
import { InArea } from "./ui";
import { locate } from "./nav";

/** The admin home data the shell already polls (to-do counts, status), shared so a section page never polls it a second time. */
export const HomeData = createContext<AdminHome | null>(null);

export function useHomeData(): AdminHome | null {
  return useContext(HomeData);
}

/** The to-do items for some keys, in that order, leaving out empty ones. */
export function useTodo(keys: string[]): TodoItem[] {
  const home = useHomeData();
  const all = home?.todo ?? [];
  return keys.map((k) => all.find((t) => t.key === k)).filter((t): t is TodoItem => !!t && t.count > 0);
}

/** The panel an old address points at (/brain opens the Content page with "AI voice" open). */
export function useHubFocus(): string | undefined {
  const { pathname } = useLocation();
  return locate(pathname).page.panel;
}

/** One section on one page (owner-approved redesign, 2026-11-22): a heading, then cards and panels. No tabs. */
export function Hub({ title, about, actions, children }: { title: string; about: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="hub">
      <header className="hub-head">
        <div>
          <h1>{title}</h1>
          <p className="muted">{about}</p>
        </div>
        {actions && <div className="hub-actions">{actions}</div>}
      </header>
      {children}
    </div>
  );
}

export function HubCards({ children }: { children: ReactNode }) {
  return <div className="hub-cards">{children}</div>;
}

/** A compact card: what it is, the live numbers, and the actions that make sense right here. `to` opens the full tool. */
export function HubCard({ title, to, open = "Open", count, tone, children, actions }: {
  title: string; to?: string; open?: string; count?: number; tone?: "warn" | "good" | "bad"; children?: ReactNode; actions?: ReactNode;
}) {
  return (
    <section className={`hub-card${tone ? ` hub-card-${tone}` : ""}`} aria-label={title}>
      <div className="hub-card-head">
        <h2>{title}</h2>
        {count !== undefined && count > 0 && <span className="hub-count" aria-label={`${count} waiting`}>{count}</span>}
      </div>
      {children && <div className="hub-card-body">{children}</div>}
      {(actions || to) && (
        <div className="hub-card-actions">
          {actions}
          {to && <Link className="btn btn-sm" to={to}>{open}</Link>}
        </div>
      )}
    </section>
  );
}

const PanelFocus = createContext<string | undefined>(undefined);

/** Gives the panels of one page the panel to open first (from the address). */
export function HubPanels({ focus, children }: { focus?: string; children: ReactNode }) {
  return <PanelFocus.Provider value={focus}><div className="hub-panels">{children}</div></PanelFocus.Provider>;
}

/** A section of the page that folds away. Its content (an existing page) is only loaded once it is opened, and it opens by itself
 *  when the address points at it. The embedded page's own title is hidden: the panel's heading names it. */
export function HubPanel({ id, title, summary, defaultOpen = false, children }: {
  id: string; title: string; summary?: ReactNode; defaultOpen?: boolean; children: ReactNode;
}) {
  const focus = useContext(PanelFocus);
  const focused = focus === id;
  const [open, setOpen] = useState(defaultOpen || focused);
  const ref = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    if (!focused) return;
    setOpen(true);
    ref.current?.scrollIntoView?.({ block: "start" });
  }, [focused]);

  return (
    <details ref={ref} id={`panel-${id}`} className="hub-panel" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>
        <span className="hub-panel-title">{title}</span>
        {summary && <span className="hub-panel-summary">{summary}</span>}
      </summary>
      {open && (
        <div className="hub-panel-body">
          <InArea.Provider value={true}>
            <Suspense fallback={<div className="page-loading" aria-busy="true" />}>{children}</Suspense>
          </InArea.Provider>
        </div>
      )}
    </details>
  );
}

/** The waiting items of a section as one list (one row per kind, each with its own action). */
export function TodoList({ items, empty }: { items: TodoItem[]; empty: string }) {
  if (!items.length) return <p className="muted small">{empty}</p>;
  return (
    <ul className="hub-todo">
      {items.map((t) => (
        <li key={t.key}>
          <span className="hub-todo-count">{t.count}</span>
          <span className="hub-todo-label">{t.label}</span>
          <Link className="btn btn-sm" to={t.to}>Review</Link>
        </li>
      ))}
    </ul>
  );
}

/** A fold inside a page (setup, advanced, rarely used): its content is only mounted once opened, so it costs nothing until then. */
export function Fold({ title, summary, defaultOpen = false, children }: { title: string; summary?: ReactNode; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details className="fold" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{title}{summary && <span className="fold-summary">{summary}</span>}</summary>
      {open && <div className="fold-body">{children}</div>}
    </details>
  );
}
