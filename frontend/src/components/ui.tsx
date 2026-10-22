import { createContext, useContext, useState, type ReactNode } from "react";
import { explainError } from "../api/client";

/** True inside an admin area whose header (tabs + one line about the page) already names the page. */
export const InArea = createContext(false);

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  if (useContext(InArea)) return actions ? <div className="page-actions page-actions-only">{actions}</div> : null;  // the area header says it already
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Card({ title, children, actions, className = "" }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export type Tone = "good" | "warn" | "bad" | "neutral" | "info";

export function Stat({ label, value, hint, tone = "neutral" }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value tone-${tone}`}>{value}</span>
      {hint && <span className="stat-hint">{hint}</span>}
    </div>
  );
}

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge tone-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="banner banner-error" role="alert">
      {message}
    </div>
  );
}

/** Banner for an ApiError, with a title that names the failing layer. */
export function ApiErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  const e = explainError(error);
  return (
    <div className={`banner ${e.kind === "model_unavailable" || e.kind === "paused" ? "banner-warn" : "banner-error"}`} role="alert">
      <strong>{e.title}.</strong> {e.message}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}

/** Tabs. The everyday tabs sit in a row; tabs marked `more` go under a "More" menu (still one click away). */
export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { key: T; label: string; more?: boolean }[]; value: T; onChange: (key: T) => void }) {
  const main = tabs.filter((t) => !t.more), more = tabs.filter((t) => t.more);
  const inMore = more.find((t) => t.key === value);
  const tab = (t: { key: T; label: string }) => (
    <button key={t.key} role="tab" aria-selected={value === t.key} className={`tab ${value === t.key ? "active" : ""}`} onClick={(e) => {
      onChange(t.key);
      e.currentTarget.closest("details")?.removeAttribute("open");
    }}>
      {t.label}
    </button>
  );
  return (
    <div className="tabs" role="tablist">
      {main.map(tab)}
      {inMore && tab(inMore)}
      {more.length > 0 && (
        <details className="tab-more">
          <summary className="tab">More ▾</summary>
          <div className="tab-more-menu">{more.map(tab)}</div>
        </details>
      )}
    </div>
  );
}

/** Technical extras, folded away until asked for. */
export function Advanced({ label = "Show technical details", children }: { label?: string; children: ReactNode }) {
  return (
    <details className="advanced">
      <summary>{label}</summary>
      <div className="stack">{children}</div>
    </details>
  );
}

/** Edit a list of short strings, one per line. */
export function ListEditor({ value, onChange, placeholder, rows = 4 }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; rows?: number }) {
  const [text, setText] = useState(value.join("\n"));
  return (
    <textarea
      className="input textarea"
      rows={rows}
      value={text}
      placeholder={placeholder ?? "One item per line"}
      onChange={(e) => {
        setText(e.target.value);
        onChange(e.target.value.split("\n").map((s) => s.trim()).filter(Boolean));
      }}
    />
  );
}

export function Field({ label, hint, children, empty }: { label: string; hint?: string; children: ReactNode; empty?: boolean }) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {empty && <span className="field-empty">not set</span>}
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return formatTime(iso);
}

export function ms(value: number | null | undefined): string {
  if (value == null) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

export function label(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const statusTone: Record<string, Tone> = {
  active: "good",
  idle: "neutral",
  paused: "warn",
  error: "bad",
  online: "good",
  offline: "bad",
  model_missing: "warn",
  connected: "good",
  not_connected: "neutral",
  disabled: "neutral",
  info: "info",
  warning: "warn",
  audit: "neutral",
  draft: "neutral",
  queued: "info",
  pending_approval: "warn",
  approved: "good",
  published: "good",
  rejected: "bad",
  brand: "info",
  community: "good",
  social: "warn",
  conversation: "neutral",
};

export const modelStateLabel: Record<string, string> = {
  online: "Online",
  model_missing: "Model not installed",
  offline: "Offline",
};
