import { describe, expect, it } from "vitest";
import raw from "../../vercel.json?raw";

// The backend (app/security/client_ip.py) believes Vercel's report of the visitor's address only when the request carries
// x-pc-edge = PROXY_SHARED_SECRET. This route transform is what adds it on the way to Railway.

type Transform = { type: string; op: string; target?: { key: unknown }; args?: string | string[]; env?: string[] };
type Route = { src: string; dest?: string; continue?: boolean; transforms?: Transform[] };
type Rewrite = { source: string; destination: string };
const config = JSON.parse(raw) as { routes?: Route[]; rewrites: Rewrite[] };

const edgeRoute = () => (config.routes ?? []).find((r) => r.transforms?.some((t) => t.target?.key === "x-pc-edge"));

describe("vercel.json: shared proxy secret on /api", () => {
  it("sets x-pc-edge from the PROXY_SHARED_SECRET env var, replacing any caller copy", () => {
    const route = edgeRoute();
    expect(route).toBeDefined();
    const ts = route!.transforms!.filter((t) => t.target?.key === "x-pc-edge");
    const set = ts.find((t) => t.op === "set")!;
    expect(set.type).toBe("request.headers");
    expect(set.args).toBe("$PROXY_SHARED_SECRET");
    expect(set.env).toEqual(["PROXY_SHARED_SECRET"]);
    // a caller's own x-pc-edge is dropped before ours is set
    expect(ts.findIndex((t) => t.op === "delete")).toBeLessThan(ts.indexOf(set));
  });

  it("only adds the header and lets routing continue (the /api rewrite and Vercel's own /api functions keep working)", () => {
    const route = edgeRoute()!;
    expect(route.continue).toBe(true);
    expect(route.dest).toBeUndefined();
    const re = new RegExp(route.src);
    for (const p of ["/api/health/public", "/api/members/auth/google/start", "/api/admin/login"]) expect(re.test(p)).toBe(true);
    for (const p of ["/", "/earn", "/assets/index.js", "/apiary"]) expect(re.test(p)).toBe(false);
    expect(config.rewrites.some((r) => r.source === "/api/:path*" && r.destination.endsWith("/api/:path*"))).toBe(true);
  });
});
