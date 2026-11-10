/**
 * Kiwi VPN 2.0 decisions, kept free of Tauri so they're tested on any machine:
 * Smart Connect (which server), failover (when to retry or switch), the speed test's numbers and split tunneling's routes.
 * The measuring itself (timings, downloads) is done by the app's Rust side; the service owns the tunnel and the kill switch.
 */

export interface SmartServer { id: number; online: boolean; load?: number; live_load?: number; recommended?: boolean }

/** A full server counts as this much slower, so a quiet server a little further away wins over a crowded close one. */
export const BUSY_PENALTY_MS = 300;

export function median(xs: (number | null | undefined)[]): number | null {
  const v = xs.filter((x): x is number => typeof x === "number" && Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

/** Lower is better. A server that couldn't be measured, or is offline, is never Smart Connect's pick. */
export function smartScore(s: SmartServer, ms: number | null | undefined): number {
  if (ms == null || !Number.isFinite(ms) || !s.online) return Infinity;
  const busy = Math.min(1, Math.max(0, s.live_load ?? s.load ?? 0));
  return ms + busy * BUSY_PENALTY_MS;
}

/** How busy a server is, 0..1; unknown counts as half full, so a server known to be quiet beats one we know nothing about. */
export function busyOf(s: SmartServer): number {
  const v = s.live_load ?? s.load;
  return typeof v === "number" && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0.5;
}

/**
 * Online servers best first. Measured ones by score (time + load), then unmeasured ones by load, then recommended first,
 * so there's always an order: with no timings at all (nothing answered, or nothing to time) it falls back to load alone.
 */
export function rankServers<T extends SmartServer>(servers: T[], timings: Record<number, number | null>): T[] {
  const key = (s: T) => {
    const score = smartScore(s, timings[s.id]);
    return Number.isFinite(score) ? [0, score, 0, s.id] : [1, busyOf(s), s.recommended ? 0 : 1, s.id];
  };
  return servers.filter((s) => s.online).sort((a, b) => {
    const ka = key(a), kb = key(b);
    for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i];
    return 0;
  });
}

// ----- failover -----

/**
 * `graceSeconds`: how long a stale handshake is waited out before acting (WireGuard's own signal, slow: a tunnel only counts
 * as down once its handshake is 180 s old). `switchGraceSeconds`: how long a server just switched to gets to answer for the
 * first time before the next one is tried (a working server handshakes in a second or two).
 */
export interface FailoverPolicy { enabled: boolean; graceSeconds: number; retriesBeforeSwitch: number; maxSwitches: number; switchGraceSeconds: number }
export const DEFAULT_FAILOVER: FailoverPolicy = { enabled: true, graceSeconds: 45, retriesBeforeSwitch: 1, maxSwitches: 3, switchGraceSeconds: 15 };

/** `settled`: this server has answered since the app moved to it (or since the person connected). */
export interface FailoverState { serverId: number | null; downSince: number | null; retries: number; switches: number; tried: number[]; settled?: boolean }
export const freshFailover = (serverId: number | null): FailoverState => ({ serverId, downSince: null, retries: 0, switches: 0, tried: serverId == null ? [] : [serverId], settled: false });

export type FailoverStep =
  | { kind: "wait" }
  | { kind: "retry"; serverId: number }
  | { kind: "switch"; serverId: number }
  | { kind: "give-up"; reason: string };

/**
 * One look at the connection while the person wants to be connected. `healthy` is the service's verdict (a fresh handshake).
 * `dead` is the liveness probe's verdict (see `observeRx`): the server stopped answering through the tunnel, confirmed by several
 * probes in a row, so there's nothing to wait out and retrying the same server would only waste time: the next server is tried
 * at once. The kill switch, not this, keeps traffic from leaking while it decides: switching only happens after the old tunnel is
 * paused (closed, with the kill switch still blocking).
 */
export function nextFailoverStep(p: FailoverPolicy, st: FailoverState, healthy: boolean, nowMs: number, ranked: SmartServer[], dead = false): { state: FailoverState; step: FailoverStep } {
  if (healthy && !dead) return { state: { ...st, downSince: null, retries: 0, settled: true }, step: { kind: "wait" } };
  if (st.serverId == null) return { state: st, step: { kind: "give-up", reason: "No server chosen." } };
  const downSince = st.downSince ?? nowMs;
  const state = { ...st, downSince };
  const confirmed = dead && p.enabled;
  // a server just switched to that hasn't answered yet: a short grace, not the long one meant for a working tunnel's blip,
  // and no retry of it (it never answered, so trying it again only wastes time)
  const freshSwitch = st.switches > 0 && !st.settled;
  const grace = freshSwitch ? Math.min(p.switchGraceSeconds, p.graceSeconds) : p.graceSeconds;
  if (!confirmed && nowMs - downSince < grace * 1000) return { state, step: { kind: "wait" } };
  if (!confirmed && !freshSwitch && (!p.enabled || st.retries < p.retriesBeforeSwitch)) {
    if (!p.enabled && st.retries >= p.retriesBeforeSwitch) return { state, step: { kind: "give-up", reason: "The server isn't answering. Automatic switching is off." } };
    return { state: { ...state, retries: st.retries + 1, downSince: nowMs }, step: { kind: "retry", serverId: st.serverId } };
  }
  if (!p.enabled) return { state, step: { kind: "give-up", reason: "The server isn't answering. Automatic switching is off." } };
  if (st.switches >= p.maxSwitches) return { state, step: { kind: "give-up", reason: "No server answered. Check your internet connection." } };
  const next = ranked.find((s) => s.online && !st.tried.includes(s.id));
  if (!next) return { state, step: { kind: "give-up", reason: "No other server is online right now." } };
  return { state: { serverId: next.id, downSince: nowMs, retries: 0, switches: st.switches + 1, tried: [...st.tried, next.id], settled: false }, step: { kind: "switch", serverId: next.id } };
}

/**
 * The order failover tries servers in: servers this PC already holds a working device for come first (switching to them needs
 * no new device, so the device limit can't refuse it and no slot is used up), each group best first as ranked.
 */
export function failoverOrder<T extends SmartServer>(ranked: T[], known: number[] | null | undefined): T[] {
  const k = new Set(known ?? []);
  return [...ranked.filter((s) => k.has(s.id)), ...ranked.filter((s) => !k.has(s.id))];
}

// ----- liveness: is the server still answering through the tunnel? -----

/**
 * WireGuard only handshakes about every two minutes, so a dead server takes minutes to show in the handshake's age. Faster: watch
 * the bytes coming in. Anything received from the server (data, a handshake, a keepalive) proves it's alive. When nothing has come
 * in for `idleSeconds`, the app sends a tiny probe through the tunnel (a DNS question to the tunnel's own resolver, sent from the
 * tunnel's own address, so it can't leave any other way) and waits for the answer. Only `failuresToDeclareDead` unanswered probes
 * in a row, `probeEverySeconds` apart, with nothing received in between, count as dead. A quiet but working link answers the first
 * probe, so being idle never triggers a switch.
 */
export interface LivenessPolicy { idleSeconds: number; probeEverySeconds: number; failuresToDeclareDead: number }
export const DEFAULT_LIVENESS: LivenessPolicy = { idleSeconds: 12, probeEverySeconds: 4, failuresToDeclareDead: 3 };

export interface LivenessState { rx: number | null; rxAt: number | null; failures: number; inFlight: boolean; lastProbeAt: number | null; dead: boolean }
export const freshLiveness = (): LivenessState => ({ rx: null, rxAt: null, failures: 0, inFlight: false, lastProbeAt: null, dead: false });

/** One reading of the tunnel's received-bytes counter. Says whether to send a probe now. */
export function observeRx(p: LivenessPolicy, st: LivenessState, rx: number, nowMs: number): { state: LivenessState; probe: boolean } {
  // the first reading, or a counter that went backwards (a new tunnel): start over from here
  if (st.rx == null || st.rxAt == null || rx < st.rx) return { state: { ...freshLiveness(), rx, rxAt: nowMs }, probe: false };
  if (rx > st.rx) return { state: { ...st, rx, rxAt: nowMs, failures: 0, dead: false }, probe: false };
  if (st.dead || st.inFlight) return { state: st, probe: false };
  const idle = nowMs - st.rxAt >= p.idleSeconds * 1000;
  const due = st.lastProbeAt == null || nowMs - st.lastProbeAt >= p.probeEverySeconds * 1000;
  if (idle && due) return { state: { ...st, inFlight: true, lastProbeAt: nowMs }, probe: true };
  return { state: st, probe: false };
}

/** A probe's answer. `answered` is null when the app couldn't probe (it doesn't know the tunnel's addresses): never a failure. */
export function probeResult(p: LivenessPolicy, st: LivenessState, answered: boolean | null, nowMs: number): LivenessState {
  if (!st.inFlight) return st; // a probe for a tunnel that has since been replaced
  if (answered !== false) return { ...st, inFlight: false, failures: 0, rxAt: nowMs, dead: false };
  const failures = st.failures + 1;
  return { ...st, inFlight: false, failures, dead: failures >= p.failuresToDeclareDead };
}

// ----- speed test -----

/** Megabits per second from bytes moved in ms. */
export function mbps(bytes: number, ms: number): number {
  if (!(bytes > 0) || !(ms > 0)) return 0;
  return Math.round(((bytes * 8) / (ms / 1000) / 1e6) * 10) / 10;
}

export interface SpeedResult { pingMs: number | null; downMbps: number; upMbps: number }
export function speedSummary(r: SpeedResult): string {
  const ping = r.pingMs == null ? "–" : `${Math.round(r.pingMs)} ms`;
  return `${r.downMbps.toFixed(1)} Mbps down · ${r.upMbps.toFixed(1)} Mbps up · ${ping}`;
}

// ----- split tunneling: addresses that skip the VPN -----

export interface Cidr { ip: number; bits: number }

export function parseCidr(raw: string): Cidr | null {
  const m = raw.trim().match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:\/(\d{1,2}))?$/);
  if (!m) return null;
  const o = m.slice(1, 5).map(Number);
  const bits = m[5] === undefined ? 32 : Number(m[5]);
  if (o.some((x) => x > 255) || bits > 32) return null;
  const ip = ((o[0] << 24) >>> 0) + (o[1] << 16) + (o[2] << 8) + o[3];
  const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
  return { ip: (ip & mask) >>> 0, bits };
}

export function formatCidr(c: Cidr): string {
  return `${c.ip >>> 24}.${(c.ip >>> 16) & 255}.${(c.ip >>> 8) & 255}.${c.ip & 255}/${c.bits}`;
}

/** Ranges that must always stay in the tunnel, whatever is excluded: excluding them would leak (0.0.0.0/0 itself, the VPN's DNS). */
export function validateExclusions(lines: string[], keepInside: string[] = []): { ok: Cidr[]; errors: string[] } {
  const ok: Cidr[] = [];
  const errors: string[] = [];
  const inside = keepInside.map(parseCidr).filter((c): c is Cidr => !!c);
  for (const raw of lines.map((l) => l.trim()).filter(Boolean)) {
    const c = parseCidr(raw);
    if (!c) { errors.push(`"${raw}" isn't an IPv4 address or range like 192.168.1.0/24`); continue; }
    if (c.bits < 8) { errors.push(`${raw} is too wide: it would send most of your traffic outside the VPN`); continue; }
    if (inside.some((k) => overlaps(k, c))) { errors.push(`${raw} includes an address that must stay in the VPN`); continue; }
    ok.push(c);
  }
  return { ok, errors };
}

function overlaps(a: Cidr, b: Cidr): boolean {
  const bits = Math.min(a.bits, b.bits);
  const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
  return ((a.ip & mask) >>> 0) === ((b.ip & mask) >>> 0);
}

/**
 * WireGuard has no "except": the tunnel's AllowedIPs must list everything that goes in. This returns 0.0.0.0/0 minus the
 * excluded ranges as the smallest list of CIDRs. IPv6 stays ::/0 (all in the tunnel, so it's refused there, never leaked).
 */
export function allowedIpsExcluding(excluded: Cidr[]): string[] {
  let blocks: Cidr[] = [{ ip: 0, bits: 0 }];
  for (const ex of excluded) {
    const next: Cidr[] = [];
    for (const b of blocks) next.push(...subtract(b, ex));
    blocks = next;
  }
  return [...blocks.sort((x, y) => x.ip - y.ip || x.bits - y.bits).map(formatCidr), "::/0"];
}

function subtract(block: Cidr, ex: Cidr): Cidr[] {
  if (!overlaps(block, ex)) return [block];
  if (ex.bits <= block.bits) return []; // the whole block is excluded
  const half = block.bits + 1;
  const lo: Cidr = { ip: block.ip, bits: half };
  const hi: Cidr = { ip: (block.ip + 2 ** (32 - half)) >>> 0, bits: half };
  return [...subtract(lo, ex), ...subtract(hi, ex)];
}

// ----- leak check verdict -----

export interface LeakInput { ipv4: string | null; ipv6: string | null; dns: string[]; tunnel_ip: string | null; expected_dns: string[] }
export interface LeakLine { ok: boolean; text: string }

/**
 * What the leak check found, in plain words. Passing means: the public IPv4 is the server's, not the person's normal one;
 * no IPv6 address is visible (Kiwi refuses IPv6 in the tunnel); and every DNS server that answered is one the VPN chose.
 */
export function leakVerdict(r: LeakInput, normalIp: string | null): { ok: boolean; lines: LeakLine[] } {
  const lines: LeakLine[] = [];
  const v4ok = !!r.ipv4 && r.ipv4 !== normalIp && (!r.tunnel_ip || r.ipv4 === r.tunnel_ip);
  lines.push({ ok: v4ok, text: !r.ipv4 ? "IPv4: no answer (are you online?)" : v4ok ? `IPv4: ${r.ipv4}, the VPN server's address` : `IPv4: ${r.ipv4}, not the VPN server's address: traffic may be leaving outside the VPN` });
  lines.push({ ok: !r.ipv6, text: r.ipv6 ? `IPv6: ${r.ipv6} is visible: IPv6 traffic is leaving outside the VPN` : "IPv6: none visible (blocked, so it can't leak)" });
  const unexpected = r.dns.filter((d) => !r.expected_dns.some((e) => sameResolver(d, e)));
  const dnsOk = r.dns.length > 0 && unexpected.length === 0;
  lines.push({ ok: dnsOk, text: !r.dns.length ? "DNS: couldn't tell which servers answered" : dnsOk ? `DNS: ${r.dns.join(", ")}, chosen by the VPN` : `DNS: ${unexpected.join(", ")} answered too: lookups may be leaking` });
  return { ok: lines.every((l) => l.ok), lines };
}

/** A resolver matches if it's the same address, or the same provider's network (anycast resolvers answer from many addresses). */
function sameResolver(seen: string, expected: string): boolean {
  if (seen === expected) return true;
  if (expected.endsWith("*")) return seen.startsWith(expected.slice(0, -1));
  // the leak check reports "address (country - provider)"; a provider name ("cloudflare", "quad9") matches by name
  if (!/[0-9:]/.test(expected)) return seen.toLowerCase().includes(expected.toLowerCase());
  return seen.split(" ")[0] === expected;
}
