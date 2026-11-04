import { Flag, place, type MapServer } from "./vpnMap";
import { site } from "./websiteData";

/**
 * The hero picture: the mascot standing on a slice of kiwi fruit, with traffic running from a laptop, a phone and a browser through it and out to the real server
 * locations (none invented: no servers, no location labels). It's a picture of how the product works, never a status: nothing here says this visitor is connected.
 */

const SEEDS = Array.from({ length: 26 }, (_, i) => (i * 360) / 26);
const SIDES: [number, number][] = [[592, 112], [612, 298], [572, 470]];
const FROM: [number, number][] = [[58, 124], [34, 292], [78, 452]];
const curve = ([x, y]: [number, number]) => `M${x} ${y}C${(x + 320) / 2} ${y} ${(x + 320) / 2 + (x < 320 ? 30 : -30)} 280 320 280`;

function Glyph({ at, kind }: { at: [number, number]; kind: "laptop" | "phone" | "browser" }) {
  return (
    <g transform={`translate(${at[0] - 24} ${at[1] - 24})`} className="vpn-art-node">
      <circle cx="24" cy="24" r="24" />
      <g fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        {kind === "laptop" && <><rect x="14" y="15" width="20" height="14" rx="2" /><path d="M10 33h28" /></>}
        {kind === "phone" && <><rect x="17" y="12" width="14" height="24" rx="3" /><path d="M22.5 32h3" /></>}
        {kind === "browser" && <><rect x="12" y="14" width="24" height="20" rx="3" /><path d="M12 20h24M16 17h.01M19.5 17h.01" /></>}
      </g>
    </g>
  );
}

export function HeroArt({ servers }: { servers: MapServer[] }) {
  const shown = servers.filter((s) => s.online).slice(0, SIDES.length);
  return (
    <div className="vpn-art" aria-hidden data-pause-offscreen>
      <svg className="vpn-art-net" viewBox="0 0 640 560" focusable="false">
        <circle className="vpn-art-orbit" cx="320" cy="280" r="252" />
        <circle className="vpn-art-orbit is-far" cx="320" cy="280" r="304" />
        {FROM.map((p, i) => <path key={i} className="vpn-art-line" d={curve(p)} />)}
        {FROM.map((p, i) => <path key={i} className="vpn-art-flow" style={{ animationDelay: `${i * -1.1}s` }} d={curve(p)} />)}
        {shown.map((_, i) => <path key={i} className="vpn-art-line" d={curve(SIDES[i])} />)}
        {shown.map((_, i) => <path key={i} className="vpn-art-flow is-out" style={{ animationDelay: `${i * -1.4}s` }} d={curve(SIDES[i])} />)}
        <g className="vpn-art-devices">
          <Glyph at={FROM[0]} kind="laptop" /><Glyph at={FROM[1]} kind="phone" /><Glyph at={FROM[2]} kind="browser" />
        </g>
        <g className="vpn-art-slice">
          <circle cx="320" cy="280" r="212" fill="#2a4a1c" />
          <circle cx="320" cy="280" r="202" fill="url(#vpn-flesh)" />
          {Array.from({ length: 52 }, (_, i) => (i * 360) / 52).map((a) => <path key={a} d="M320 214V96" stroke="#f1ffc4" strokeOpacity=".2" strokeWidth="2" strokeLinecap="round" transform={`rotate(${a} 320 280)`} />)}
          {SEEDS.map((a, i) => <ellipse key={a} cx="320" cy={i % 2 ? 158 : 176} rx="4.8" ry="10" fill="#14210c" transform={`rotate(${a} 320 280)`} />)}
          <circle cx="320" cy="280" r="74" fill="#f0fbc8" fillOpacity=".78" />
        </g>
        <defs>
          <radialGradient id="vpn-flesh"><stop offset="0" stopColor="#d8f57a" /><stop offset=".62" stopColor="#b1ea45" /><stop offset="1" stopColor="#84c51f" /></radialGradient>
        </defs>
      </svg>
      <img className="vpn-art-kiwi" src={site.kiwi.src} width={site.kiwi.width} height={site.kiwi.height} alt="" />
      {shown.map((s, i) => {
        const p = place(s);
        return (
          <span key={s.id} className="vpn-art-place" style={{ left: `${(SIDES[i][0] / 640) * 100}%`, top: `${(SIDES[i][1] / 560) * 100}%` }}>
            <Flag cc={s.country_code} />{p.city || p.country}
          </span>
        );
      })}
    </div>
  );
}
