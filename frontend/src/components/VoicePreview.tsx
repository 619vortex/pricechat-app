import { useEffect, useState } from "react";

/** The clip of a narrator voice (made by worker/voice_previews.py; slug = backend app/content/voices.py preview_slug). */
export const voiceClip = (key: string) => `/voices/${key.toLowerCase().replace(/[^a-z0-9_]/g, "-")}.mp3`;

let current: HTMLAudioElement | null = null; // one clip at a time across every menu on the page

/** ▶ next to a voice menu: plays a 5-second sample of the chosen voice. Nothing for "auto" or presets without a clip. */
export function VoicePreview({ voice }: { voice?: string | null }) {
  const [playing, setPlaying] = useState(false);
  const [missing, setMissing] = useState(false);
  useEffect(() => { setMissing(false); setPlaying(false); }, [voice]);
  if (!voice || voice === "auto" || missing) return null;
  const toggle = () => {
    if (playing && current) { current.pause(); setPlaying(false); return; }
    current?.pause();
    const a = new Audio(voiceClip(voice));
    current = a;
    a.onended = () => setPlaying(false);
    a.onpause = () => setPlaying(false);
    a.onerror = () => { setPlaying(false); setMissing(true); };
    setPlaying(true);
    a.play().catch(() => setPlaying(false));
  };
  return (
    <button type="button" className="btn btn-ghost btn-sm" onClick={toggle} aria-label={playing ? "Stop the voice sample" : "Play a sample of this voice"}
            title={playing ? "Stop" : "Hear this voice"}>{playing ? "■" : "▶"}</button>
  );
}
