import React from "react";
import { Composition } from "remotion";
import { Editorial } from "./editorial/Editorial";
import type { EditorialProps } from "./editorial/types";
import type { Props } from "./types";
import { Video } from "./Video";

const SAMPLE: Props = {
  theme: "pokemon", gold: true, intensity: "normal", captionStyle: "pop", duration: 6,
  brand: { name: "Price Chat", site: "pricechat.live", cta: "Follow for daily market stories." },
  scenes: [
    { kind: "punchline", start: 0, end: 3, transition: "cut", screen: "Sample storyboard", data: {} },
    { kind: "outro", start: 3, end: 6, transition: "whip", data: { kiwi: "kiwi.png", cta: "Follow for daily market stories." } },
  ],
  words: [{ t: "Sample", s: 0.2, e: 0.6, p: 0 }, { t: "words.", s: 0.6, e: 1.0, p: 0, hl: true }],
  beats: [], audio: { voice: "sfx/pop.wav", music: null, musicGain: 0.15, sfx: [] }, assets: {},
};

const EDITORIAL_SAMPLE: EditorialProps = {
  style: { id: "real_news", label: "Real news", font: "sans", accent: "#19e68c", ink: "#ffffff", frameBg: "#0b0b0c", transition: "cut", dissolve: 0,
           move: 0.04, captions: "clean", captionSize: 54, grade: "", creditCorner: "top", frameTop: 330, frameHeight: 900, backdrop: "blur" },
  duration: 4, brand: { name: "Price Chat", site: "pricechat.live" },
  assets: { k: { src: "kiwi.png", kind: "image", w: 512, h: 512, credit: "" } },
  video: [{ id: "c1", asset: "k", start: 0, dur: 4, fit: "frame", focus: [0.5, 0.5], zoom: 1, move: "push", gain: 0 }],
  overlays: [{ id: "o1", type: "lower_third", start: 0.5, dur: 3, data: { name: "Sample", detail: "Editorial timeline" } }],
  captions: { words: [{ t: "Sample", s: 0.2, e: 0.6, p: 0 }, { t: "words.", s: 0.6, e: 1.0, p: 0 }] },
  narration: { src: null, gain: 1 }, music: { src: null, gain: 0 }, endCard: 0,
};

export const Root: React.FC = () => (
  <>
    <Composition id="PriceChat" component={Video} width={1080} height={1920} fps={30} durationInFrames={180} defaultProps={SAMPLE}
                 calculateMetadata={({ props }) => ({ durationInFrames: Math.max(30, Math.round(props.duration * 30)) })} />
    {/* Content Studio v4: the editorial timeline (real footage, quiet graphics) */}
    <Composition id="Editorial" component={Editorial} width={1080} height={1920} fps={30} durationInFrames={120} defaultProps={EDITORIAL_SAMPLE}
                 calculateMetadata={({ props }) => ({ durationInFrames: Math.max(30, Math.round(props.duration * 30)) })} />
  </>
);
