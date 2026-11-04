import { useEffect, useMemo, useState } from "react";

/**
 * The Global Network map. Everything on it comes from the server list: a new server in a new country gets a lit country and a pin with no change here, and the map
 * zooms out to fit. A pin's position is the only thing this file knows: a known city, else the middle of its country. It's a location, never a measurement.
 */

export interface MapServer { id: number; name: string; region: string; country_code: string; online: boolean }

/** Where a server is, for people: "Falkenstein" and "Germany" from whatever Admin typed ("Germany" + "Falkenstein, Germany", or "Helsinki 1" + "Finland"). */
export function place(x: Pick<MapServer, "name" | "region" | "country_code">): { city: string; country: string } {
  const [first, ...rest] = (x.region || "").split(",").map((p) => p.trim()).filter(Boolean);
  const named = countryName(x.country_code);
  if (rest.length) return { city: first, country: rest[rest.length - 1] };
  const city = (x.name || "").replace(/\s*#?\d+$/, "").trim();
  const country = first || named;
  return { city: city && city !== country ? city : "", country };
}

/** "Helsinki, Finland", or just the country when no city is known. */
export function placeLine(x: Pick<MapServer, "name" | "region" | "country_code">): string {
  const p = place(x);
  return p.city ? `${p.city}, ${p.country}` : p.country;
}

function countryName(cc: string): string {
  const code = (cc || "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) return "";
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code; } catch { return code; }
}

/** A real flag picture: Windows can't draw emoji flags and shows "FI" instead. A country with no picture here gets its two letters. */
export function Flag({ cc }: { cc: string }) {
  const code = (cc || "").trim().toLowerCase();
  const [broken, setBroken] = useState(false);
  if (!/^[a-z]{2}$/.test(code) || broken) return <span className="vpn-flag is-code" aria-hidden>{code.toUpperCase() || "–"}</span>;
  return <span className="vpn-flag" aria-hidden><img src={`/brand/flags/${code}.svg`} alt="" width={24} height={18} loading="lazy" onError={() => setBroken(true)} /></span>;
}

/** Cities worth an exact pin. Any other server still gets one, in the middle of its country. */
const CITIES: Record<string, [number, number]> = {
  helsinki: [60.17, 24.94], falkenstein: [50.48, 12.37], nuremberg: [49.45, 11.08], frankfurt: [50.11, 8.68], amsterdam: [52.37, 4.9], london: [51.51, -0.13], paris: [48.86, 2.35],
  stockholm: [59.33, 18.07], oslo: [59.91, 10.75], zurich: [47.38, 8.54], madrid: [40.42, -3.7], ashburn: [39.04, -77.49], hillsboro: [45.52, -122.99], "new york": [40.71, -74.01],
  singapore: [1.35, 103.82], tokyo: [35.68, 139.69], sydney: [-33.87, 151.21], auckland: [-36.85, 174.76], dubai: [25.2, 55.27], toronto: [43.65, -79.38],
};

type World = { step: number; top: number; columns: number; rows: string[] };
export interface MapPin { id: number; x: number; y: number; online: boolean; label: string; cc: string }
export interface MapDrawing { land: string; lit: string; off: string; dot: number; pins: MapPin[] }

const W = 1000, H = 660;
const MIN_LAT_SPAN = 34;

function decodeRow(row: string, columns: number): string[] {
  const cells: string[] = [];
  for (const m of row.matchAll(/([A-Za-z.]{2})(\d+)/g)) for (let i = 0; i < Number(m[2]); i++) cells.push(m[1]);
  while (cells.length < columns) cells.push("..");
  return cells;
}

/** Works out the view that fits the servers, then one path of dots each for the land, the lit countries and the countries whose servers are all offline. */
export function drawMap(world: World, servers: MapServer[]): MapDrawing {
  const grid = world.rows.map((r) => decodeRow(r, world.columns));
  const lonOf = (c: number) => -180 + (c + 0.5) * world.step;
  const latOf = (r: number) => world.top - (r + 0.5) * world.step;
  const centres = new Map<string, [number, number]>();
  const centreOf = (cc: string): [number, number] | null => {
    if (centres.has(cc)) return centres.get(cc)!;
    let lat = 0, lon = 0, n = 0;
    grid.forEach((cells, r) => cells.forEach((v, c) => { if (v === cc) { lat += latOf(r); lon += lonOf(c); n++; } }));
    if (!n) return null;
    const out: [number, number] = [lat / n, lon / n];
    centres.set(cc, out);
    return out;
  };
  const located = servers.flatMap((s) => {
    const cc = (s.country_code || "").trim().toUpperCase();
    const at = CITIES[place(s).city.toLowerCase()] ?? centreOf(cc);
    return at ? [{ s, cc, lat: at[0], lon: at[1] }] : [];
  });
  const lats = located.map((p) => p.lat), lons = located.map((p) => p.lon);
  const midLat = located.length ? (Math.min(...lats) + Math.max(...lats)) / 2 : 20;
  const midLon = located.length ? (Math.min(...lons) + Math.max(...lons)) / 2 : 10;
  const cos = Math.max(0.35, Math.cos((midLat * Math.PI) / 180));
  const latSpanNeeded = located.length ? Math.max(...lats) - Math.min(...lats) : 130;
  const lonSpanNeeded = located.length ? Math.max(...lons) - Math.min(...lons) : 340;
  const latSpan = Math.min(134, Math.max(MIN_LAT_SPAN, latSpanNeeded * 1.6, ((lonSpanNeeded * 1.35 * cos) * H) / W));
  const pxLat = H / latSpan, pxLon = pxLat * cos;
  const toX = (lon: number) => W / 2 + (lon - midLon) * pxLon;
  const toY = (lat: number) => H / 2 - (lat - midLat) * pxLat;

  const rowStep = Math.max(1, Math.round(latSpan / (world.step * 62)));
  const pitch = rowStep * world.step * pxLat;
  const colStep = Math.max(1, Math.round(pitch * 1.12 / (world.step * pxLon)));
  const lit = new Set(located.filter((p) => p.s.online).map((p) => p.cc));
  const off = new Set(located.filter((p) => !p.s.online && !lit.has(p.cc)).map((p) => p.cc));
  const paths = { land: "", lit: "", off: "" };
  for (let r = 0; r < grid.length; r += rowStep) {
    const y = toY(latOf(r));
    if (y < -pitch || y > H + pitch) continue;
    const shift = (r / rowStep) % 2 ? Math.floor(colStep / 2) : 0;
    for (let c = shift; c < world.columns; c += colStep) {
      const v = grid[r][c];
      if (v === "..") continue;
      const x = toX(lonOf(c));
      if (x < -pitch || x > W + pitch) continue;
      paths[lit.has(v) ? "lit" : off.has(v) ? "off" : "land"] += `M${x.toFixed(1)} ${y.toFixed(1)}h0`;
    }
  }
  return { ...paths, dot: pitch * 0.62, pins: located.map(({ s, cc, lat, lon }) => ({ id: s.id, x: toX(lon), y: toY(lat), online: s.online, label: place(s).city || place(s).country, cc })) };
}

/** Loads its dots only when the section is on the page, so they never slow the hero. Leaves a quiet placeholder while it does. */
export function NetworkMap({ servers, focus }: { servers: MapServer[]; focus: number | null }) {
  const [world, setWorld] = useState<World | null>(null);
  useEffect(() => {
    let alive = true;
    void import("./vpnWorld").then((m) => { if (alive) setWorld(m.WORLD); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  const key = servers.map((s) => `${s.id}:${s.name}:${s.region}:${s.country_code}:${s.online}`).join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const map = useMemo(() => (world ? drawMap(world, servers) : null), [world, key]);
  return (
    <div className={`vpn-map${map ? " is-ready" : ""}`} aria-hidden data-pause-offscreen>
      {!map ? <span className="vpn-map-wait" /> : (
        <>
          <svg viewBox={`0 0 ${W} ${H}`} focusable="false">
            <g fill="none" strokeLinecap="round" strokeWidth={map.dot}>
              <path className="vpn-map-land" d={map.land} />
              <path className="vpn-map-off" d={map.off} />
              <path className="vpn-map-lit" d={map.lit} />
            </g>
          </svg>
          {map.pins.map((p) => {
            const onLeft = p.x > W * 0.62;
            return (
              <span key={p.id} className={`vpn-pin${p.online ? " is-on" : ""}${focus === p.id ? " is-focus" : ""}${onLeft ? " is-left" : ""}`} style={{ left: `${(p.x / W) * 100}%`, top: `${(p.y / H) * 100}%` }}>
                <i />
                <b><Flag cc={p.cc} />{p.label}</b>
              </span>
            );
          })}
        </>
      )}
    </div>
  );
}
