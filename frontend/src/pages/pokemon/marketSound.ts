/**
 * Market terminal sounds: tiny tones synthesised with Web Audio (no files). Same manners as the website's sound.ts:
 * nothing plays before the viewer has interacted with the page, nothing when they switched sound off, and sound is
 * decoration only: any failure is silent. The on/off choice is remembered in this browser only (separate from the
 * website's member sounds).
 */
import { useEffect, useState } from "react";

export type MarketSound = "tick" | "up" | "down" | "star" | "chime";

const KEY = "pc-market-sound";
const listeners = new Set<(on: boolean) => void>();
let ctx: AudioContext | null = null;

export function marketSoundOn(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setMarketSound(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* convenience only */
  }
  listeners.forEach((l) => l(on));
  if (on) play("chime");
}

function interacted(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation;
  return ua ? ua.hasBeenActive : false;
}

function audio(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return null;
  if (!ctx) ctx = new AC();
  if (ctx.state === "suspended") void ctx.resume().catch(() => undefined);
  return ctx;
}

/** One soft note: frequency glide from f0 to f1 with a fast attack and exponential decay. */
function note(a: AudioContext, f0: number, f1: number, start: number, dur: number, gain: number, type: OscillatorType = "sine"): void {
  const o = a.createOscillator();
  const g = a.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f0, start);
  o.frequency.exponentialRampToValueAtTime(f1, start + dur);
  g.gain.setValueAtTime(0.0001, start);
  g.gain.exponentialRampToValueAtTime(gain, start + 0.008);
  g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  o.connect(g).connect(a.destination);
  o.start(start);
  o.stop(start + dur + 0.02);
}

export function play(name: MarketSound): void {
  if (!marketSoundOn() || !interacted()) return;
  try {
    const a = audio();
    if (!a) return;
    const t = a.currentTime + 0.005;
    switch (name) {
      case "tick":
        note(a, 1800, 1400, t, 0.035, 0.035, "triangle");
        break;
      case "up":
        note(a, 660, 700, t, 0.09, 0.05);
        note(a, 880, 940, t + 0.07, 0.12, 0.05);
        break;
      case "down":
        note(a, 700, 660, t, 0.09, 0.05);
        note(a, 520, 480, t + 0.07, 0.14, 0.05);
        break;
      case "star":
        note(a, 1320, 1500, t, 0.07, 0.045);
        note(a, 1760, 1980, t + 0.05, 0.1, 0.04);
        note(a, 2350, 2640, t + 0.1, 0.14, 0.03);
        break;
      case "chime":
        note(a, 784, 784, t, 0.18, 0.045);
        note(a, 1175, 1175, t + 0.09, 0.26, 0.04);
        break;
    }
  } catch {
    /* decoration only */
  }
}

export function useMarketSound(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(marketSoundOn);
  useEffect(() => {
    listeners.add(setOn);
    return () => {
      listeners.delete(setOn);
    };
  }, []);
  return [on, setMarketSound];
}
