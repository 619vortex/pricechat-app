// The storyboard the Python worker writes (props.json). Every number or name shown on screen comes from the
// video's verified research brief; the renderer only lays it out and animates it.

export type Theme = "pokemon" | "crypto" | "trading" | "viral" | "community";
export type Intensity = "calm" | "normal" | "high";
export type CaptionStyle = "pop" | "karaoke" | "boxed" | "clean";
export type Transition = "whip" | "zoom" | "flash" | "slide" | "fade" | "cut";
export type SceneKind =
  | "price_reveal" | "card_entrance" | "card_detail" | "evidence" | "chart" | "headline"
  | "stat" | "punchline" | "list" | "outro" | "coin" | "photo";

// a freely licensed photograph (Openverse / Wikimedia Commons), shown with its credit line
export interface Photo { src: string; ratio: number; credit: string }

export interface Word {
  t: string;   // the written word (captions keep "$17,225", the voice says "seventeen thousand...")
  s: number;   // start, seconds
  e: number;   // end, seconds
  hl?: boolean; // an important word: highlighted in captions
  p: number;   // caption phrase index (the worker groups words into short phrases)
}

export interface Scene {
  kind: SceneKind;
  start: number; // seconds: the scene owns the screen from start to end
  end: number;
  transition: Transition; // how this scene enters
  screen?: string; // the segment's on-screen words (approved with the script)
  data: Record<string, unknown>;
}

export interface Sfx { src: string; at: number; gain: number }

export type Props = {
  theme: Theme;
  gold: boolean; // a rare / high-value collectible: gold accents
  intensity: Intensity;
  captionStyle: CaptionStyle;
  duration: number; // seconds
  brand: { name: string; site: string; cta?: string };
  scenes: Scene[];
  words: Word[];
  beats: number[]; // music beat times (seconds), for beat-synced accents
  audio: { voice: string; music: string | null; musicGain: number; sfx: Sfx[] };
  assets: { card?: string; cardRatio?: number; coin?: string }; // coin: the coin's logo (CoinGecko)
  safe?: boolean; // draw the TikTok UI safe-zone guides (preview only)
};
