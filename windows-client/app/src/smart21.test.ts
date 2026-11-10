// Kiwi VPN 2.1: fast failover (liveness probing), known servers first, Smart Connect's load fallback.
import { describe, expect, it } from "vitest";
import { DEFAULT_FAILOVER, DEFAULT_LIVENESS, busyOf, failoverOrder, freshFailover, freshLiveness, nextFailoverStep, observeRx, probeResult, rankServers, type LivenessState } from "./smart";

const hel = { id: 1, online: true, live_load: 0.2 };
const fsn = { id: 2, online: true, live_load: 0.1 };
const ash = { id: 3, online: true, live_load: 0.7 };
const t0 = 1_000_000;

/** Feeds one reading a second for `seconds`, answering every probe with `answer`. Returns the state and when it went dead (ms after start). */
function run(seconds: number, rxAt: (s: number) => number, answer: boolean | null, start = freshLiveness()) {
  let st: LivenessState = start;
  let deadAt: number | null = null;
  let probes = 0;
  for (let s = 0; s <= seconds; s++) {
    const now = t0 + s * 1000;
    const o = observeRx(DEFAULT_LIVENESS, st, rxAt(s), now);
    st = o.state;
    if (o.probe) { probes++; st = probeResult(DEFAULT_LIVENESS, st, answer, now + 1500); }
    if (st.dead && deadAt == null) deadAt = s * 1000;
  }
  return { st, deadAt, probes };
}

describe("liveness", () => {
  it("a server that stops answering is declared dead within about 25 seconds", () => {
    // bytes arrive until second 10, then nothing; every probe goes unanswered
    const r = run(60, (s) => (s <= 10 ? s * 100 : 1000), false);
    expect(r.deadAt).not.toBeNull();
    expect(r.deadAt!).toBeLessThanOrEqual(10_000 + 25_000);
    expect(r.deadAt!).toBeGreaterThanOrEqual(10_000 + DEFAULT_LIVENESS.idleSeconds * 1000);
  });

  it("an idle but working link is never declared dead: it answers the probe", () => {
    const r = run(600, () => 500, true);
    expect(r.st.dead).toBe(false);
    expect(r.probes).toBeGreaterThan(0);
    // and it isn't probed constantly: about once per idle period
    expect(r.probes).toBeLessThanOrEqual(Math.ceil(600 / DEFAULT_LIVENESS.idleSeconds) + 1);
  });

  it("a busy link is never probed", () => {
    expect(run(300, (s) => s * 1000, false).probes).toBe(0);
  });

  it("when the app can't probe (it doesn't know the tunnel's addresses), nothing is declared dead", () => {
    expect(run(600, () => 0, null).st.dead).toBe(false);
  });

  it("bytes arriving between failed probes clear the failures (a short blip doesn't add up)", () => {
    let st = observeRx(DEFAULT_LIVENESS, freshLiveness(), 100, t0).state;
    st = observeRx(DEFAULT_LIVENESS, st, 100, t0 + 13_000).state;
    st = probeResult(DEFAULT_LIVENESS, st, false, t0 + 14_000);
    st = observeRx(DEFAULT_LIVENESS, st, 100, t0 + 17_000).state;
    st = probeResult(DEFAULT_LIVENESS, st, false, t0 + 18_000);
    expect(st.failures).toBe(2);
    st = observeRx(DEFAULT_LIVENESS, st, 160, t0 + 19_000).state; // something came back
    expect(st.failures).toBe(0);
    expect(st.dead).toBe(false);
  });

  it("a new tunnel (counter went backwards) starts over, and a late probe answer for the old one is ignored", () => {
    let st = observeRx(DEFAULT_LIVENESS, freshLiveness(), 5000, t0).state;
    const o = observeRx(DEFAULT_LIVENESS, st, 5000, t0 + 20_000);
    expect(o.probe).toBe(true);
    st = observeRx(DEFAULT_LIVENESS, o.state, 10, t0 + 21_000).state; // new tunnel
    expect(st).toMatchObject({ rx: 10, failures: 0, inFlight: false, dead: false });
    expect(probeResult(DEFAULT_LIVENESS, st, false, t0 + 22_000)).toEqual(st);
  });

  it("only one probe at a time", () => {
    let o = observeRx(DEFAULT_LIVENESS, observeRx(DEFAULT_LIVENESS, freshLiveness(), 1, t0).state, 1, t0 + 13_000);
    expect(o.probe).toBe(true);
    o = observeRx(DEFAULT_LIVENESS, o.state, 1, t0 + 30_000);
    expect(o.probe).toBe(false);
  });
});

describe("failover with a confirmed dead server", () => {
  const ranked = [hel, fsn, ash];

  it("switches at once: no grace period and no retry of the dead server", () => {
    const r = nextFailoverStep(DEFAULT_FAILOVER, { ...freshFailover(1), settled: true }, true, t0, ranked, true);
    expect(r.step).toEqual({ kind: "switch", serverId: 2 });
    expect(r.state).toMatchObject({ serverId: 2, switches: 1, tried: [1, 2], settled: false });
  });

  it("a server just switched to gets a short grace to answer, then the next is tried without a retry", () => {
    let r = nextFailoverStep(DEFAULT_FAILOVER, freshFailover(1), true, t0, ranked, true);
    expect(r.step).toEqual({ kind: "switch", serverId: 2 });
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 10_000, ranked);
    expect(r.step).toEqual({ kind: "wait" });
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 10_000 + DEFAULT_FAILOVER.switchGraceSeconds * 1000 + 1, ranked);
    expect(r.step).toEqual({ kind: "switch", serverId: 3 });
  });

  it("once the new server answers it's settled: a later blip gets the full grace and a retry again", () => {
    let r = nextFailoverStep(DEFAULT_FAILOVER, freshFailover(1), true, t0, ranked, true);
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, true, t0 + 3000, ranked);
    expect(r.state.settled).toBe(true);
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 60_000, ranked);
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 60_000 + 20_000, ranked);
    expect(r.step).toEqual({ kind: "wait" });
    r = nextFailoverStep(DEFAULT_FAILOVER, r.state, false, t0 + 60_000 + 46_000, ranked);
    expect(r.step).toEqual({ kind: "retry", serverId: 2 });
  });

  it("dead is ignored when switching is off (the person chose to stay on their server)", () => {
    const off = { ...DEFAULT_FAILOVER, enabled: false };
    const r = nextFailoverStep(off, freshFailover(1), true, t0, ranked, true);
    expect(r.step).toEqual({ kind: "wait" });
  });

  it("still gives up rather than looping", () => {
    let st = freshFailover(1);
    const seen: string[] = [];
    for (let i = 0; i < 6; i++) {
      const r = nextFailoverStep(DEFAULT_FAILOVER, st, true, t0 + i, ranked, true);
      st = r.state;
      seen.push(r.step.kind);
    }
    expect(seen).toEqual(["switch", "switch", "give-up", "give-up", "give-up", "give-up"]);
  });
});

describe("known servers first", () => {
  it("puts servers this PC already has a device for first, keeping the ranking inside each group", () => {
    expect(failoverOrder([hel, fsn, ash], [3]).map((s) => s.id)).toEqual([3, 1, 2]);
    expect(failoverOrder([hel, fsn, ash], [3, 2]).map((s) => s.id)).toEqual([2, 3, 1]);
    expect(failoverOrder([hel, fsn, ash], undefined).map((s) => s.id)).toEqual([1, 2, 3]);
  });

  it("a failover picks a known server over a better-ranked unknown one", () => {
    const order = failoverOrder([hel, fsn, ash], [3]);
    const r = nextFailoverStep(DEFAULT_FAILOVER, freshFailover(1), true, t0, order, true);
    expect(r.step).toEqual({ kind: "switch", serverId: 3 });
  });
});

describe("Smart Connect without timings", () => {
  it("falls back to load: the quietest first, unknown load counted as half full", () => {
    expect(rankServers([hel, fsn, ash], {}).map((s) => s.id)).toEqual([2, 1, 3]);
    expect(busyOf({ id: 9, online: true })).toBe(0.5);
    expect(rankServers([{ id: 9, online: true }, ash, hel], {}).map((s) => s.id)).toEqual([1, 9, 3]);
  });

  it("measured servers combine time and load, ahead of unmeasured ones", () => {
    expect(rankServers([hel, fsn, ash], { 3: 20, 1: 80 }).map((s) => s.id)).toEqual([1, 3, 2]); // 80+60 < 20+210
  });
});
