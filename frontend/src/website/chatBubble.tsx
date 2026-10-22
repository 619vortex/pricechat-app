import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** Bottom-right chat bubble: a live, read-only preview of the public Price Chat Telegram group, with the way in
 * ("Open chat in Telegram"). Messages are polled from our own API (first names only); nothing is sent from here. */

interface ChatMessage { id: number; name: string; text: string; at: string; reply_to: number | null; forwarded: boolean }
interface ChatFeed {
  enabled: boolean;
  title?: string;
  username?: string;
  link?: string;
  members?: number | null;
  messages?: ChatMessage[];
  last_message_at?: string | null;
}

const OPEN_POLL = 8_000;
const CLOSED_POLL = 60_000;
const SEEN_KEY = "pc-chat-seen";
const PLANE = "M21.9 4.6 18.8 19.3c-.2 1-.8 1.3-1.7.8l-4.6-3.4-2.2 2.1c-.2.2-.5.5-1 .5l.3-4.7 8.6-7.8c.4-.3-.1-.5-.6-.2L6.9 13.3 2.4 11.9c-1-.3-1-1 .2-1.5L20.5 3.5c.8-.3 1.6.2 1.4 1.1Z";

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

const HUES = [152, 200, 265, 330, 28, 48, 180, 10];
function hue(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return HUES[h % HUES.length];
}

function readSeen(): number {
  try {
    return Number(localStorage.getItem(SEEN_KEY) ?? 0);
  } catch {
    return 0;
  }
}

export function ChatBubble() {
  const [feed, setFeed] = useState<ChatFeed | null>(null);
  const [open, setOpen] = useState(false);
  // tucked away while the page scrolls DOWN (reading / tapping a mission), back as soon as it scrolls up or stops near the top
  const [tucked, setTucked] = useState(false);
  useEffect(() => {
    let last = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      if (Math.abs(y - last) < 8) return;
      setTucked(y > last && y > 160);
      last = y;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  const [seen, setSeen] = useState(readSeen);
  const list = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    try {
      const r = await fetch("/api/website/chat", { headers: { Accept: "application/json" } });
      if (r.ok) setFeed((await r.json()) as ChatFeed);
    } catch {
      /* offline: keep what we have */
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), open ? OPEN_POLL : CLOSED_POLL);
    return () => window.clearInterval(t);
  }, [open, load]);

  const msgs = feed?.messages ?? [];
  const newest = msgs.length ? Date.parse(msgs[msgs.length - 1].at) : 0;
  const unread = open ? 0 : msgs.filter((m) => Date.parse(m.at) > seen).length;
  const live = newest > 0 && Date.now() - newest < 15 * 60_000;

  useEffect(() => {
    if (!open || !newest) return;
    setSeen(newest);
    try {
      localStorage.setItem(SEEN_KEY, String(newest));
    } catch {
      /* fine */
    }
  }, [open, newest]);

  useEffect(() => {
    if (open && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [open, msgs.length]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);

  if (!feed?.enabled || typeof document === "undefined") return null;
  const byId = new Map(msgs.map((m) => [m.id, m]));
  return createPortal(
    <div className={`cb${tucked && !open ? " is-tucked" : ""}${open ? " is-open" : ""}`}>
      {open && (
        <section className="cb-panel" role="dialog" aria-label="Price Chat community chat">
          <header className="cb-head">
            <span className="cb-avatar" aria-hidden><svg viewBox="0 0 24 24"><path d={PLANE} /></svg></span>
            <div className="cb-titles">
              <strong>{feed.title ?? "Price Chat"}</strong>
              <span>
                {live && <i className="cb-live" aria-hidden />}
                {live ? "Live" : "Telegram community"}
                {feed.members ? ` · ${feed.members.toLocaleString("en-GB")} members` : ""}
              </span>
            </div>
            <button type="button" className="cb-x" aria-label="Close chat" onClick={() => setOpen(false)}>×</button>
          </header>
          <div className="cb-list" ref={list} aria-live="polite">
            {msgs.length === 0 ? (
              <p className="cb-empty">It's quiet right now. Be the first to say hi in the Telegram group.</p>
            ) : msgs.map((m) => {
              const parent = m.reply_to ? byId.get(m.reply_to) : undefined;
              return (
                <div key={m.id} className="cb-msg">
                  <div className="cb-meta">
                    <b style={{ color: `hsl(${hue(m.name)} 75% 62%)` }}>{m.name}</b>
                    <time dateTime={m.at} title={new Date(m.at).toLocaleString("en-GB")}>{ago(m.at)}</time>
                  </div>
                  {parent && <div className="cb-reply">↪ {parent.name}: {parent.text.slice(0, 80)}</div>}
                  <p className="cb-text">{m.text}</p>
                </div>
              );
            })}
          </div>
          <footer className="cb-foot">
            <a className="cb-join" href={feed.link} target="_blank" rel="noopener noreferrer">
              <svg viewBox="0 0 24 24" aria-hidden><path d={PLANE} /></svg>
              Open chat in Telegram
            </a>
            <p>Live preview of our public Telegram group. Join to chat.</p>
          </footer>
        </section>
      )}
      <button type="button" className={`cb-fab${open ? " is-open" : ""}`} aria-expanded={open}
              aria-label={open ? "Close community chat" : `Open community chat${unread ? `, ${unread} new messages` : ""}`}
              onClick={() => setOpen((v) => !v)}>
        {open ? <span className="cb-fab-x" aria-hidden>×</span> : <svg viewBox="0 0 24 24" aria-hidden><path d={PLANE} /></svg>}
        {!open && unread > 0 && <span className="cb-badge">{unread > 9 ? "9+" : unread}</span>}
        {!open && live && unread === 0 && <span className="cb-dot" aria-hidden />}
      </button>
    </div>,
    document.body,
  );
}
