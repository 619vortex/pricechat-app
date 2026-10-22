/**
 * Reward sounds (Kiwi Passport, Pack Giveaway): ten small local files in /website/sounds (scripts/brand/make-sounds.py, make-pack-sounds.py), native Audio.
 * Never loaded or played before the member has interacted with the page (browser autoplay rules, and good manners);
 * never when they've switched sound off. The on/off choice is remembered in this browser only.
 */
import { useEffect, useState } from "react";

export type Sound = "xp" | "tick" | "levelup" | "streak" | "rip" | "shine" | "swish" | "rise" | "chase" | "first" | "coin" | "cash" | "listed" | "nope";

const KEY = "pc-sound";
const VOLUME: Record<Sound, number> = { xp: 0.55, tick: 0.35, levelup: 0.6, streak: 0.55, rip: 0.75, shine: 0.55, swish: 0.45, rise: 0.5, chase: 0.7, first: 0.75, coin: 0.6, cash: 0.65, listed: 0.55, nope: 0.5 };
const cache = new Map<Sound, HTMLAudioElement>();
const listeners = new Set<(on: boolean) => void>();

export function soundOn(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setSoundOn(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* a convenience only */
  }
  listeners.forEach((l) => l(on));
}

/** The member has clicked, tapped or typed on this page (sticky user activation). */
function interacted(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation;
  return ua ? ua.hasBeenActive : touched;
}
let touched = false;
if (typeof document !== "undefined") {
  const mark = () => {
    touched = true;
  };
  document.addEventListener("pointerdown", mark, { once: true, capture: true });
  document.addEventListener("keydown", mark, { once: true, capture: true });
}

export function playSound(name: Sound): void {
  if (!soundOn() || !interacted() || typeof Audio === "undefined") return;
  try {
    let a = cache.get(name);
    if (a && a.paused === false && a.currentTime > 0 && (name === "swish" || name === "tick")) {
      a = new Audio(`/website/sounds/${name}.wav`);  // still playing: a second player so quick repeats overlap
    } else if (!a) {
      a = new Audio(`/website/sounds/${name}.wav`);
      a.preload = "auto";
      cache.set(name, a);
    }
    a.volume = VOLUME[name];
    a.currentTime = 0;
    void a.play()?.catch?.(() => undefined);
  } catch {
    /* sound is decoration: never break the page */
  }
}

/** Load the files once the member has pressed something, so the first ding is on time. */
export function warmSounds(): void {
  if (!soundOn() || typeof Audio === "undefined") return;
  for (const name of ["xp", "tick", "levelup", "streak", "rip", "shine", "swish", "rise", "chase", "first", "coin", "cash", "listed", "nope"] as Sound[]) {
    if (cache.has(name)) continue;
    try {
      const a = new Audio(`/website/sounds/${name}.wav`);
      a.preload = "auto";
      cache.set(name, a);
    } catch {
      return;
    }
  }
}

export function useSoundPref(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(soundOn);
  useEffect(() => {
    listeners.add(setOn);
    return () => {
      listeners.delete(setOn);
    };
  }, []);
  return [on, setSoundOn];
}
