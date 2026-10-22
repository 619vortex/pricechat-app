import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Loading, formatTime } from "./ui";

/** Settings › Background jobs: where the owner's private Telegram alerts go (app/alerts.py). */
export function OwnerAlertsCard() {
  const { data, reload } = useApi(api.ownerAlerts);
  const [chat, setChat] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { if (data) setChat(data.telegram_chat); }, [data?.telegram_chat]);

  async function act(fn: () => Promise<unknown>, done: string) {
    try {
      await fn();
      setError(null);
      setNote(done);
      await reload();
    } catch (e) {
      setError(e);
      setNote(null);
    }
  }

  if (!data) return <Card title="Owner alerts"><Loading /></Card>;
  return (
    <Card title="Owner alerts" actions={<Badge tone={data.enabled && data.telegram_chat ? "good" : "neutral"}>{data.enabled && data.telegram_chat ? "On" : "Log only"}</Badge>}>
      <p className="muted">
        A private Telegram message from the bot when a background job fails 3 runs in a row (the nightly backup: its first failure),
        when it works again, and when a Brand Manager TikTok account signs out while AUTO is on. The same alert at most once every 6 h.
        Your numeric Telegram user id (ask @userinfobot); press Start in a private chat with the bot once.
      </p>
      <div className="row">
        <input value={chat} onChange={(e) => setChat(e.target.value.trim())} placeholder="Telegram user id, e.g. 123456789" inputMode="numeric" />
        <button className="btn btn-sm btn-primary" disabled={chat === data.telegram_chat}
          onClick={() => void act(() => api.updateOwnerAlerts({ telegram_chat: chat }), "Saved")}>Save</button>
        <button className="btn btn-sm" disabled={!data.telegram_chat} onClick={() => void act(api.testOwnerAlert, "Test alert sent: check Telegram")}>Send test</button>
        <button className="btn btn-sm" onClick={() => void act(() => api.updateOwnerAlerts({ enabled: !data.enabled }), data.enabled ? "Alerts off" : "Alerts on")}>
          {data.enabled ? "Turn off" : "Turn on"}
        </button>
      </div>
      {note && <p className="muted">{note}</p>}
      <ApiErrorBanner error={error} />
      {data.recent.length > 0 && (
        <ul className="muted">
          {data.recent.map((a) => (
            <li key={a.key}>{formatTime(a.last_sent_at)} · {a.text.split("\n")[0]}{a.open ? " (still open)" : ""}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}
