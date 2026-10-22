import { useEffect, useRef, useState } from "react";
import { memberApi } from "../members";
import { playSound } from "../sound";
import { fmt, type Ev } from "./api";
import "./toasts.css";

const SEEN = "pc-kb-notif-seen";

function seen(): number {
  try {
    return Number(localStorage.getItem(SEEN) ?? 0) || 0;
  } catch {
    return 0;
  }
}

function icon(e: Ev): string {
  const after = String(e.data.after ?? "");
  return after === "ACTIVE" ? "🟢" : after === "TARGET_HIT" ? "✅" : after === "STOP_HIT" ? "🛑" : after === "CLOSED" ? "⏱" : after === "EXPIRED" ? "⌛" : "📣";
}

/** Website notifications when a signal is published, opens or closes: real signal events from the Kiwi Brain log, the same
 *  ones the Telegram group is told about. The first visit only remembers where it is (no flood of old news). */
export function SignalToasts({ onOpen }: { onOpen?: (e: Ev) => void }) {
  const [items, setItems] = useState<Ev[]>([]);
  const last = useRef<number>(seen());
  useEffect(() => {
    let live = true;
    const load = async () => {
      if (document.hidden) return;
      try {
        const r = await memberApi<{ items: Ev[]; last_id: number }>(`/members/kiwi-brain/notifications?after_id=${last.current}`);
        if (!live) return;
        const fresh = last.current > 0 ? r.items : [];  // first visit: start from now
        const top = Math.max(last.current, r.last_id, ...r.items.map((x) => x.id));
        last.current = top;
        try { localStorage.setItem(SEEN, String(top)); } catch { /* blocked: fine */ }
        if (fresh.length) {
          setItems((cur) => [...cur, ...fresh].slice(-4));
          // Earn is quiet except when a signal opens or closes (owner's call, 2026-10-18)
          const moved = fresh.filter((x) => ["ACTIVE", "TARGET_HIT", "STOP_HIT", "CLOSED"].includes(String(x.data.after ?? "")));
          if (moved.length) playSound(moved.some((x) => x.data.after === "STOP_HIT") ? "nope" : "coin");
        }
      } catch {
        /* signed out or offline: no notifications, nothing invented */
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 30_000);
    return () => { live = false; window.clearInterval(id); };
  }, []);
  useEffect(() => {
    if (!items.length) return;
    const id = window.setTimeout(() => setItems((cur) => cur.slice(1)), 12_000);
    return () => window.clearTimeout(id);
  }, [items]);
  if (!items.length) return null;
  return (
    <div className="kbn" role="status" aria-live="polite">
      {items.map((e) => (
        <button key={e.id} className={`kbn-item is-${String(e.data.after ?? "pub").toLowerCase()}`} onClick={() => { onOpen?.(e); setItems((c) => c.filter((x) => x.id !== e.id)); }}>
          <span className="kbn-ic" aria-hidden>{icon(e)}</span>
          <span className="kbn-txt"><b>{e.title}</b><small>{fmt.time(e.at)} · signal update</small></span>
        </button>
      ))}
    </div>
  );
}
