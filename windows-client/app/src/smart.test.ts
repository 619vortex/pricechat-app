import { describe, expect, it } from "vitest";
import { BUSY_PENALTY_MS, DEFAULT_FAILOVER, allowedIpsExcluding, freshFailover, mbps, median, nextFailoverStep, parseCidr, rankServers, smartScore, speedSummary, validateExclusions } from "./smart";

const hel = { id: 1, online: true, live_load: 0 };
const fsn = { id: 2, online: true, live_load: 0, recommended: true };
const ash = { id: 3, online: true, live_load: 0 };

describe("Smart Connect", () => {
  it("takes the median, ignoring failed samples", () => {
    expect(median([30, 10, 20])).toBe(20);
    expect(median([10, 20, 30, 40])).toBe(25);
    expect(median([null, undefined, NaN, 50])).toBe(50);
    expect(median([])).toBeNull();
  });

  it("prefers the fastest, but a crowded server loses to a quiet one a little further away", () => {
    expect(rankServers([hel, fsn, ash], { 1: 40, 2: 60, 3: 120 }).map((s) => s.id)).toEqual([1, 2, 3]);
    expect(rankServers([{ ...hel, live_load: 1 }, fsn, ash], { 1: 40, 2: 60, 3: 120 }).map((s) => s.id)).toEqual([2, 3, 1]);
    expect(smartScore({ online: true, id: 9, load: 3 }, 10)).toBe(10 + BUSY_PENALTY_MS); // load capped at 1
  });

  it("never picks an offline or unmeasured server ahead of a measured one, and still gives an order with no timings", () => {
    expect(rankServers([{ ...hel, online: false }, fsn, ash], { 1: 5, 2: 60, 3: 120 }).map((s) => s.id)).toEqual([2, 3]);
    expect(rankServers([hel, fsn, ash], { 3: 200 }).map((s) => s.id)).toEqual([3, 2, 1]); // then recommended, then id
    expect(rankServers([hel, fsn, ash], {}).map((s) => s.id)).toEqual([2, 1, 3]);
  });
});

describe("failover", () => {
  const ranked = [hel, fsn, ash];
  const t0 = 1_000_000;

  it("waits out a short blip, retries the same server once, then switches to the next best", () => {
    let st = freshFailover(1);
    let r = nextFailoverStep(DEFAULT_FAILOVER, st, false, t0, ranked);
    expect(r.step).toEqual({ kind: "wait" });
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 30_000, ranked);
    expect(r.step).toEqual({ kind: "wait" }); // still inside the grace period
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 46_000, ranked);
    expect(r.step).toEqual({ kind: "retry", serverId: 1 });
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 46_000 + 46_000, ranked);
    expect(r.step).toEqual({ kind: "switch", serverId: 2 });
    st = r.state;
    expect(st.tried).toEqual([1, 2]);
  });

  it("a healthy reading resets the clock", () => {
    let r = nextFailoverStep(DEFAULT_FAILOVER, freshFailover(1), false, t0, ranked);
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, true, t0 + 40_000, ranked);
    expect(r.state.downSince).toBeNull();
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 50_000, ranked);
    expect(r.step).toEqual({ kind: "wait" });
  });

  it("stops after the last switch, never loops, and never switches when switching is off", () => {
    const p = { ...DEFAULT_FAILOVER, retriesBeforeSwitch: 0, graceSeconds: 0 };
    let r = nextFailoverStep(p, freshFailover(1), false, t0, ranked);
    expect(r.step).toEqual({ kind: "switch", serverId: 2 });
    r = nextFailoverStep(p, r.state, false, t0 + 1, ranked);
    expect(r.step).toEqual({ kind: "switch", serverId: 3 });
    r = nextFailoverStep(p, r.state, false, t0 + 2, ranked);
    expect(r.step.kind).toBe("give-up");
    const off = { ...DEFAULT_FAILOVER, enabled: false, graceSeconds: 0 };
    r = nextFailoverStep(off, freshFailover(1), false, t0, ranked);
    expect(r.step).toEqual({ kind: "retry", serverId: 1 });
    r = nextFailoverStep(off, r.state, false, t0 + 1, ranked);
    expect(r.step).toEqual({ kind: "give-up", reason: "The server isn't answering. Automatic switching is off." });
  });

  it("gives up cleanly when nothing else is online", () => {
    const p = { ...DEFAULT_FAILOVER, retriesBeforeSwitch: 0, graceSeconds: 0 };
    const r = nextFailoverStep(p, freshFailover(1), false, t0, [hel]);
    expect(r.step).toEqual({ kind: "give-up", reason: "No other server is online right now." });
  });
});

describe("speed test", () => {
  it("turns bytes and time into Mbps", () => {
    expect(mbps(12_500_000, 1000)).toBe(100);
    expect(mbps(0, 1000)).toBe(0);
    expect(mbps(1000, 0)).toBe(0);
    expect(speedSummary({ pingMs: 41.6, downMbps: 93.25, upMbps: 18 })).toBe("93.3 Mbps down · 18.0 Mbps up · 42 ms");
  });
});

describe("split tunneling", () => {
  it("reads ranges and refuses the ones that would leak or are too wide", () => {
    expect(parseCidr("192.168.1.0/24")).toEqual({ ip: 0xc0a80100, bits: 24 });
    expect(parseCidr("10.1.2.3")).toEqual({ ip: 0x0a010203, bits: 32 });
    expect(parseCidr("300.1.1.1")).toBeNull();
    const v = validateExclusions(["192.168.1.0/24", "nonsense", "0.0.0.0/0", "10.66.0.0/16"], ["10.66.0.1/32"]);
    expect(v.ok.map((c) => c.ip)).toEqual([0xc0a80100]);
    expect(v.errors).toHaveLength(3);
  });

  it("builds the tunnel's routes as everything except the excluded ranges, IPv6 always inside", () => {
    expect(allowedIpsExcluding([])).toEqual(["0.0.0.0/0", "::/0"]);
    const routes = allowedIpsExcluding([parseCidr("192.168.0.0/16")!]);
    expect(routes).not.toContain("192.168.0.0/16");
    expect(routes).toContain("0.0.0.0/1");
    expect(routes).toContain("192.0.0.0/9");
    expect(routes[routes.length - 1]).toBe("::/0");
    // the pieces cover exactly 2^32 - 2^16 addresses
    const size = routes.slice(0, -1).reduce((n, r) => n + 2 ** (32 - Number(r.split("/")[1])), 0);
    expect(size).toBe(2 ** 32 - 2 ** 16);
  });
});

import { leakVerdict } from "./smart";

describe("leak verdict", () => {
  const base = { ipv4: "178.156.252.184", ipv6: null, dns: ["178.156.252.184"], tunnel_ip: "178.156.252.184", expected_dns: ["178.156.252.184"] };
  it("passes only when IPv4 is the server's, no IPv6 shows and DNS is the VPN's", () => {
    expect(leakVerdict(base, "5.30.213.225").ok).toBe(true);
    expect(leakVerdict({ ...base, ipv4: "5.30.213.225" }, "5.30.213.225").ok).toBe(false);
    expect(leakVerdict({ ...base, ipv6: "2a00:f2a::1" }, "5.30.213.225").lines[1].text).toMatch(/leaving outside/);
    const dnsLeak = leakVerdict({ ...base, dns: ["178.156.252.184", "94.200.200.200"] }, "5.30.213.225");
    expect(dnsLeak.ok).toBe(false);
    expect(dnsLeak.lines[2].text).toContain("94.200.200.200");
    expect(leakVerdict({ ...base, dns: ["2620:fe::9"], expected_dns: ["2620:fe::*"] }, null).lines[2].ok).toBe(true);
    // shared servers use Cloudflare's DNS through the tunnel; the check reports "address (country - provider)"
    expect(leakVerdict({ ...base, dns: ["172.71.1.2 (Finland - Cloudflare)"], expected_dns: ["cloudflare"] }, null).ok).toBe(true);
    expect(leakVerdict({ ...base, dns: ["94.200.1.1 (United Arab Emirates - du)"], expected_dns: ["cloudflare"] }, null).ok).toBe(false);
    expect(leakVerdict({ ...base, dns: ["178.156.252.184 (United States - Hetzner)"] }, null).ok).toBe(true);
  });
});
