// Renders one storyboard: node render.mjs <props.json> <public dir> <out.mp4> [--still <frame> <out.png>]...
// The worker writes props.json and copies the voice, card image, fonts, music and sound effects into <public dir>.
// Prints one JSON line per progress step and a final {"done": ...} line with timings.
import { bundle } from "@remotion/bundler";
import { ensureBrowser, renderMedia, renderStill, selectComposition } from "@remotion/renderer";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const [propsPath, publicDir, out, ...rest] = process.argv.slice(2);
if (!propsPath || !publicDir || !out) {
  console.error("usage: node render.mjs <props.json> <public dir> <out.mp4> [--still <frame> <out.png>]... [--no-video]");
  process.exit(2);
}
const stills = [];
let video = true;
let comp = "PriceChat"; // PriceChat = v2 motion graphics; Editorial = v4 editorial timeline (--comp Editorial)
for (let i = 0; i < rest.length; i++) {
  if (rest[i] === "--still") { stills.push([Number(rest[i + 1]), rest[i + 2]]); i += 2; }
  else if (rest[i] === "--no-video") video = false;
  else if (rest[i] === "--comp") { comp = rest[i + 1]; i += 1; }
}
const inputProps = JSON.parse(fs.readFileSync(propsPath, "utf8"));
const t0 = Date.now();
const log = (o) => console.log(JSON.stringify(o));

await ensureBrowser();
const serveUrl = await bundle({ entryPoint: path.join(here, "src", "index.ts"), publicDir: path.resolve(publicDir) });
log({ step: "bundled", s: (Date.now() - t0) / 1000 });
const composition = await selectComposition({ serveUrl, id: comp, inputProps });
for (const [frame, file] of stills) {
  await renderStill({ composition, serveUrl, inputProps, frame, output: path.resolve(file), imageFormat: "png" });
}
if (stills.length) log({ step: "stills", n: stills.length, s: (Date.now() - t0) / 1000 });
if (video) {
  let last = -1;
  await renderMedia({
    composition, serveUrl, inputProps, codec: "h264", outputLocation: path.resolve(out), crf: 18, pixelFormat: "yuv420p",
    audioCodec: "aac", audioBitrate: "192k", concurrency: Math.max(2, Math.min(16, Math.floor(os.cpus().length / 2))),
    chromiumOptions: { gl: "angle" },
    onProgress: ({ progress }) => {
      const p = Math.floor(progress * 10);
      if (p !== last) { last = p; log({ step: "render", progress: p / 10 }); }
    },
  });
}
log({ done: true, seconds: (Date.now() - t0) / 1000, frames: composition.durationInFrames });
