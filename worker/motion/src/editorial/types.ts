// The editorial timeline (Content Studio v4): real footage and photographs on a video track, clean graphics on an
// overlay track, narration, captions, music and the clips' own sound. The Python worker (editorial.py) writes it; the
// dashboard's timeline editor changes it; this renderer only plays it back. Every number in an overlay was checked
// against the research before it got here.

export type Fit = "fill" | "frame";
export type Move = "none" | "push" | "pull" | "pan_left" | "pan_right" | "pan_up" | "pan_down";
export type OverlayType = "dim" | "title" | "lower_third" | "price" | "chart" | "facts" | "source" | "text";

export interface Asset {
  src: string; // file in the render's public folder
  kind: "video" | "image";
  w: number;
  h: number;
  duration?: number; // seconds (video)
  credit?: string; // the attribution line the licence asks for ("" when none is needed)
  audio?: boolean; // the clip has its own sound
}

export interface Clip {
  id: string;
  asset: string; // key in props.assets
  start: number; // seconds on the timeline
  dur: number;
  in?: number; // seconds into the source (video)
  fit: Fit; // fill = full-bleed crop around the focus point; frame = the whole picture inside the style's frame
  focus: [number, number]; // 0..1, the point the crop and the move keep in view
  zoom: number; // 1 = as framed; >1 = tighter (a close-up within an authentic picture)
  move: Move; // slow camera movement within the picture (never more than the style allows)
  gain: number; // the clip's own sound, 0..1 (ducked under the narration)
}

export interface Overlay {
  id: string;
  type: OverlayType;
  start: number;
  dur: number;
  data: Record<string, unknown>;
}

export interface Word { t: string; s: number; e: number; hl?: boolean; p: number }

export interface StyleSpec {
  id: string; // real_news | automotive | luxury | collectibles | tech_demo | meme
  label: string;
  font: "sans" | "serif";
  accent: string;
  ink: string; // text colour on the frame background
  frameBg: string; // what surrounds a framed picture
  transition: "cut" | "dissolve";
  dissolve: number; // frames
  move: number; // how far a move travels over a shot (0.05 = 5 %)
  captions: "clean" | "boxed" | "none";
  captionSize: number;
  grade: string; // CSS filter applied to footage ("" = untouched)
  creditCorner: "top" | "bottom";
  frameTop: number; // where a framed picture sits (px from the top) and how tall its box is
  frameHeight: number;
  backdrop: "blur" | "solid"; // behind a framed picture: a soft, dark copy of the same picture, or the plain frame colour
}

export type EditorialProps = {
  style: StyleSpec;
  duration: number;
  brand: { name: string; site: string };
  assets: Record<string, Asset>;
  video: Clip[];
  overlays: Overlay[];
  captions: { words: Word[] };
  narration: { src: string | null; gain: number };
  music: { src: string | null; gain: number };
  endCard: number; // seconds of quiet end card (0 = none)
  safe?: boolean; // draw TikTok safe-zone guides (preview only)
};
