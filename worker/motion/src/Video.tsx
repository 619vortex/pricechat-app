import React from "react";
import { AbsoluteFill, Audio, continueRender, delayRender, Img, interpolate, random, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Captions } from "./Captions";
import { Background, clamp, easeInOut, MotionBlur } from "./fx";
import { SCENES } from "./scenes";
import { C, energy, FONT, look as lookOf, SAFE } from "./theme";
import type { Props, Scene, Transition } from "./types";

const FPS = 30;
const OVERLAP: Record<Transition, number> = { whip: 9, zoom: 10, slide: 10, fade: 12, flash: 0, cut: 0 };

let fontsReady: Promise<void> | null = null;
function loadFonts(): Promise<void> {
  if (!fontsReady) {
    const faces = [new FontFace("Anton", `url(${staticFile("fonts/Anton-Regular.ttf")})`),
                   new FontFace("Inter", `url(${staticFile("fonts/Inter-Variable.ttf")})`, { weight: "100 900" })];
    fontsReady = Promise.all(faces.map((f) => f.load().then((x) => { (document.fonts as unknown as { add(f: FontFace): void }).add(x); }))).then(() => undefined);
  }
  return fontsReady;
}

/** How a scene enters (and how the one before it leaves while it does). p goes 0 → 1 over the overlap. */
function enterStyle(t: Transition, p: number): { style: React.CSSProperties; blur: number } {
  switch (t) {
    case "whip": return { style: { transform: `translateX(${(1 - p) * 1080}px)` }, blur: Math.sin(p * Math.PI) * 45 };
    case "zoom": return { style: { transform: `scale(${interpolate(p, [0, 1], [1.45, 1])})`, opacity: p }, blur: (1 - p) * 18 };
    case "slide": return { style: { transform: `translateY(${(1 - p) * 1920}px)` }, blur: Math.sin(p * Math.PI) * 30 };
    case "fade": return { style: { opacity: p }, blur: 0 };
    default: return { style: {}, blur: 0 };
  }
}

function exitStyle(t: Transition, p: number): { style: React.CSSProperties; blur: number } {
  switch (t) {
    case "whip": return { style: { transform: `translateX(${-p * 1080}px)` }, blur: Math.sin(p * Math.PI) * 45 };
    case "zoom": return { style: { transform: `scale(${interpolate(p, [0, 1], [1, 0.82])})`, opacity: 1 - p }, blur: p * 14 };
    case "slide": return { style: { transform: `translateY(${-p * 600}px)`, opacity: 1 - p * 0.7 }, blur: 0 };
    case "fade": return { style: { opacity: 1 - p }, blur: 0 };
    default: return { style: {}, blur: 0 };
  }
}

const SceneShell: React.FC<{ scene: Scene; next?: Scene; index: number; frames: number; ovIn: number; ovOut: number; props: Props }> =
  ({ scene, next, index, frames, ovIn, ovOut, props }) => {
    const f = useCurrentFrame();
    const e = energy(props);
    const lk = lookOf(props);
    const Comp = SCENES[scene.kind] ?? SCENES.punchline;
    const pin = ovIn ? interpolate(f, [0, ovIn], [0, 1], { ...clamp, easing: easeInOut }) : 1;
    const pout = ovOut ? interpolate(f, [frames - ovOut, frames], [0, 1], { ...clamp, easing: easeInOut }) : 0;
    const a = ovIn ? enterStyle(scene.transition, pin) : { style: {}, blur: 0 };
    const b = next && ovOut ? exitStyle(next.transition, pout) : { style: {}, blur: 0 };
    // the camera: a slow push or drift for the life of the scene, plus a punch on hard cuts and flashes
    const life = interpolate(f, [0, frames], [0, 1], clamp);
    const push = 1 + 0.045 * e * life;
    const drift = (index % 2 ? -1 : 1) * 18 * e * life;
    const hard = scene.transition === "cut" || scene.transition === "flash";
    const punch = hard ? interpolate(spring({ frame: f, fps: FPS, config: { damping: 12, stiffness: 200 } }), [0, 1], [1.07, 1]) : 1;
    // camera shake on hard cuts: a few frames of decaying, deterministic jitter
    const amp = hard ? interpolate(f, [0, 9], [16 * e, 0], clamp) : 0;
    const sx = amp ? (random(`sx${index}-${f}`) - 0.5) * 2 * amp : 0;
    const sy = amp ? (random(`sy${index}-${f}`) - 0.5) * 2 * amp : 0;
    const vertical = scene.transition === "slide" || next?.transition === "slide";
    return (
      <AbsoluteFill style={{ ...a.style }}>
        <AbsoluteFill style={{ ...b.style }}>
          <MotionBlur amount={a.blur + b.blur} vertical={vertical} id={`mb${index}`}>
            <AbsoluteFill style={{ transform: `scale(${push * punch}) translate(${drift + sx}px, ${sy}px)` }}>
              <Comp scene={scene} look={lk} e={e} props={props} frames={frames} index={index} />
            </AbsoluteFill>
          </MotionBlur>
        </AbsoluteFill>
      </AbsoluteFill>
    );
  };

const Flash: React.FC<{ at: number; color: string }> = ({ at, color }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [at - 2, at, at + 7], [0, 0.85, 0], clamp);
  return o > 0 ? <AbsoluteFill style={{ background: `radial-gradient(circle at 50% 40%, white, ${color})`, opacity: o, mixBlendMode: "screen" }} /> : null;
};

/** The consistent top-left brand tag, with a light swipe across it as the video opens (the intro treatment). */
const BrandTag: React.FC<{ props: Props; until: number }> = ({ props, until }) => {
  const f = useCurrentFrame();
  const lk = lookOf(props);
  if (f >= until) return null;
  const s = spring({ frame: f - 2, fps: FPS, config: { damping: 16, stiffness: 140 } });
  const sw = interpolate(f, [4, 22], [-30, 130], clamp);
  return (
    <div style={{ position: "absolute", left: SAFE.left, top: SAFE.top, display: "flex", alignItems: "center", gap: 14, opacity: s,
                  transform: `translateY(${interpolate(s, [0, 1], [-20, 0])}px)` }}>
      <Img src={staticFile("kiwi.png")} style={{ width: 58, filter: `drop-shadow(0 0 2px ${lk.accent})` }} />
      <div style={{ position: "relative", overflow: "hidden", fontFamily: FONT.ui, fontWeight: 900, fontSize: 30, letterSpacing: 3, color: C.white }}>
        PRICE <span style={{ color: lk.accent }}>CHAT</span>
        <div style={{ position: "absolute", top: 0, bottom: 0, left: `${sw}%`, width: "30%", background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.7), transparent)" }} />
      </div>
      <div style={{ marginLeft: 6, padding: "6px 14px", borderRadius: 999, border: `2px solid ${lk.accent}88`, color: lk.accent, fontFamily: FONT.ui,
                    fontWeight: 800, fontSize: 22, letterSpacing: 2 }}>{lk.label}</div>
    </div>
  );
};

const SafeGuides: React.FC = () => (
  <AbsoluteFill style={{ pointerEvents: "none" }}>
    <div style={{ position: "absolute", left: SAFE.left, top: SAFE.top, width: SAFE.right - SAFE.left, height: SAFE.contentBottom - SAFE.top, outline: "3px dashed rgba(0,255,255,0.6)" }} />
    <div style={{ position: "absolute", left: SAFE.left, top: SAFE.captionTop, width: SAFE.right - SAFE.left, height: SAFE.captionBottom - SAFE.captionTop, outline: "3px dashed rgba(255,0,255,0.7)" }} />
    <div style={{ position: "absolute", right: 0, top: 700, width: 130, height: 900, background: "rgba(255,0,0,0.18)" }} />
    <div style={{ position: "absolute", left: 0, bottom: 0, width: 1080, height: 380, background: "rgba(255,0,0,0.18)" }} />
  </AbsoluteFill>
);

export const Video: React.FC<Props> = (raw) => {
  const { durationInFrames } = useVideoConfig();
  // asset names in the storyboard are files in the render's public folder
  const props: Props = { ...raw, assets: { ...raw.assets, card: raw.assets.card ? staticFile(raw.assets.card) : undefined,
                                           coin: raw.assets.coin ? staticFile(raw.assets.coin) : undefined } };
  const [handle] = React.useState(() => delayRender("fonts"));
  React.useEffect(() => { loadFonts().then(() => continueRender(handle)).catch(() => continueRender(handle)); }, [handle]);
  const lk = lookOf(props);
  const scenes = props.scenes;
  const starts = scenes.map((s) => Math.round(s.start * FPS));
  const ends = scenes.map((s, i) => (i + 1 < scenes.length ? starts[i + 1] : durationInFrames));
  const outroAt = scenes.findIndex((s) => s.kind === "outro");
  // music: ducked under the voice (fast down, slower back up), faded in and out
  const speech = props.words.map((w) => [Math.round((w.s - 0.12) * FPS), Math.round((w.e + 0.3) * FPS)] as const);
  const talking = (f: number) => speech.some(([a, b]) => f >= a && f <= b);
  const musicVolume = (f: number) => {
    let near = 99;
    for (let d = 0; d <= 9 && near === 99; d++) if (talking(f - d) || talking(f + Math.min(d, 3))) near = d;
    const duck = near === 99 ? 1 : interpolate(near, [0, 9], [0.32, 1], clamp);
    const edges = interpolate(f, [0, 12, durationInFrames - 45, durationInFrames], [0, 1, 1, 0], clamp);
    return props.audio.musicGain * duck * edges;
  };
  return (
    <AbsoluteFill style={{ background: C.black }}>
      <Background look={lk} e={energy(props)} bgImage={props.assets.card} />
      {scenes.map((s, i) => {
        const ovIn = i ? OVERLAP[s.transition] : 0;
        const ovOut = i + 1 < scenes.length ? OVERLAP[scenes[i + 1].transition] : 0;
        const from = Math.max(0, starts[i] - ovIn);
        const frames = Math.max(1, ends[i] - from);
        return (
          <Sequence key={i} from={from} durationInFrames={frames} name={`${i} ${s.kind}`}>
            <SceneShell scene={s} next={scenes[i + 1]} index={i} frames={frames} ovIn={ovIn} ovOut={ovOut} props={props} />
          </Sequence>
        );
      })}
      {scenes.map((s, i) => (s.transition === "flash" && i ? <Flash key={`fl${i}`} at={starts[i]} color={lk.accent} /> : null))}
      <BrandTag props={props} until={outroAt >= 0 ? starts[outroAt] : durationInFrames} />
      <Captions words={props.words} style={props.captionStyle} look={lk} />
      <ProgressBar color={lk.accent} />
      {props.safe ? <SafeGuides /> : null}
      <Audio src={staticFile(props.audio.voice)} />
      {props.audio.music ? <Audio src={staticFile(props.audio.music)} volume={musicVolume} loop /> : null}
      {props.audio.sfx.map((x, i) => (
        <Sequence key={`sfx${i}`} from={Math.max(0, Math.round(x.at * FPS))} durationInFrames={Math.round(3 * FPS)} name={`sfx ${x.src}`}>
          <Audio src={staticFile(x.src)} volume={x.gain} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};

const ProgressBar: React.FC<{ color: string }> = ({ color }) => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return <div style={{ position: "absolute", left: 0, top: 0, height: 8, width: `${(f / durationInFrames) * 100}%`, background: color, boxShadow: `0 0 12px ${color}` }} />;
};
