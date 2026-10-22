import { useState } from "react";

/**
 * Small single-series charts for the Members dashboard. One hue (the dashboard accent), a recessive baseline,
 * thin marks with rounded data-ends, a per-mark hover readout, and a table view for every chart.
 */

interface Point {
  label: string;
  value: number;
}

function fmtDay(d: string): string {
  return new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

/** Change over time: thin columns, one per day. Hover (or focus) a day to read it. */
export function DailyColumns({ title, unit, points }: { title: string; unit: string; points: { day: string; members: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...points.map((p) => p.members));
  const W = 600, H = 120, gap = 2;
  const bw = (W - gap * (points.length - 1)) / points.length;
  const shown = hover ?? points.length - 1;
  return (
    <figure className="mchart">
      <figcaption className="mchart-head">
        <span>{title}</span>
        {points.length > 0 && (
          <span className="mchart-readout" aria-live="polite">
            <strong>{points[shown].members.toLocaleString("en-GB")}</strong> {unit} · {fmtDay(points[shown].day)}
          </span>
        )}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="mchart-svg" role="img" aria-label={`${title}, last ${points.length} days`} onMouseLeave={() => setHover(null)}>
        <line x1={0} x2={W} y1={H + 0.5} y2={H + 0.5} className="mchart-base" />
        {points.map((p, i) => {
          const h = p.members === 0 ? 0 : Math.max(2, (p.members / max) * (H - 4));
          const x = i * (bw + gap);
          return (
            <g key={p.day} onMouseEnter={() => setHover(i)}>
              <rect x={x} y={0} width={bw + gap} height={H} fill="transparent" />
              {h > 0 && <path className={`mchart-bar${hover === i ? " is-hover" : ""}`} d={roundedTop(x, H - h, bw, h, Math.min(4, bw / 2))} />}
            </g>
          );
        })}
        <text x={0} y={H + 14} className="mchart-tick">{points[0] ? fmtDay(points[0].day) : ""}</text>
        <text x={W} y={H + 14} className="mchart-tick" textAnchor="end">{points.length ? fmtDay(points[points.length - 1].day) : ""}</text>
      </svg>
      <TableView rows={points.map((p) => ({ label: fmtDay(p.day), value: p.members }))} unit={unit} />
    </figure>
  );
}

/** Magnitude by category: horizontal bars, each directly labelled; categories are named, not colour-coded. */
export function LabelledBars({ title, unit, rows }: { title: string; unit: string; rows: Point[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <figure className="mchart">
      <figcaption className="mchart-head">
        <span>{title}</span>
      </figcaption>
      {rows.length === 0 ? (
        <p className="muted small">Nothing yet.</p>
      ) : (
        <ul className="mbars">
          {rows.map((r) => (
            <li key={r.label} title={`${r.label}: ${r.value.toLocaleString("en-GB")} ${unit}`}>
              <span className="mbars-label">{r.label}</span>
              <span className="mbars-track">
                <span className="mbars-fill" style={{ width: r.value ? `max(4px, ${(r.value / max) * 100}%)` : 0 }} />
              </span>
              <span className="mbars-value">{r.value.toLocaleString("en-GB")}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}

function TableView({ rows, unit }: { rows: Point[]; unit: string }) {
  return (
    <details className="mchart-table">
      <summary>Table</summary>
      <table className="table">
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td>{r.label}</td>
              <td>
                {r.value.toLocaleString("en-GB")} {unit}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, h);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}
