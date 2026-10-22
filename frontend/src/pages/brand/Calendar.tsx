import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading } from "../../components/ui";
import { bm, type Video } from "./api";
import { VideoLine } from "./BrandManager";

/** The next two weeks (and the last one): every posting slot, what fills it, and what's still free. */
export function CalendarTab() {
  const { data, failure } = useApi(() => bm.calendar(14), 30000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const tz = data.timezone;
  const day = (iso: string) => new Date(iso).toLocaleDateString([], { timeZone: tz, weekday: "long", month: "short", day: "numeric" });
  const groups = new Map<string, { at: string; v: Video | null }[]>();
  for (const v of data.items) if (v.slot_at) (groups.get(day(v.slot_at)) ?? groups.set(day(v.slot_at), []).get(day(v.slot_at))!).push({ at: v.slot_at, v });
  for (const s of data.free_slots) if (!data.items.some((v) => v.slot_at === s)) (groups.get(day(s)) ?? groups.set(day(s), []).get(day(s))!).push({ at: s, v: null });
  const days = [...groups.entries()].sort((a, b) => a[1][0].at.localeCompare(b[1][0].at));
  return (
    <section className="bmx-card">
      <h2>Calendar <span className="muted">· {tz} · posting at {data.post_times.join(", ")}</span></h2>
      <p className="bmx-fine">Change the posting times, time zone and videos per day in Settings › Publishing. Free slots are filled automatically while AUTO runs.</p>
      {days.length === 0 && <p className="muted">Nothing scheduled.</p>}
      {days.map(([d, slots]) => (
        <div key={d} className="bmx-day">
          <h3>{d}</h3>
          <ul className="bmx-list">
            {slots.sort((a, b) => a.at.localeCompare(b.at)).map((s) => (
              <li key={s.at + (s.v?.id ?? "")}>
                <time>{new Date(s.at).toLocaleTimeString([], { timeZone: tz, hour: "2-digit", minute: "2-digit" })}</time>
                {s.v ? <VideoLine v={s.v} /> : <span className="muted">Free slot: a topic is picked before it comes up</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
