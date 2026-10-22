// Web copies of an image: a plain resample to WebP (no cropping, retouching or other edits). Uses headless Edge canvas.
// Usage: node scripts/brand/resize-webp.mjs <in.png> <out-prefix> <width>[,<width>...] [quality]
//   node scripts/brand/resize-webp.mjs frontend/public/website/team/kiwi.png frontend/public/website/team/kiwi 1200,640 0.86
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [inp, prefix, widths, q = "0.86"] = process.argv.slice(2);
const port = 9339;
const edge = spawn(process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  ["--headless=new", "--disable-gpu", `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "pc-rs-"))}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let t; for (let i = 0; i < 50 && !t; i++) { await sleep(200); try { t = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((x) => x.type === "page"); } catch {} }
const s = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => s.addEventListener("open", r));
let id = 0; const pend = new Map();
s.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } });
const send = (method, params) => new Promise((r) => { const i = ++id; pend.set(i, r); s.send(JSON.stringify({ id: i, method, params })); });
const src = "data:image/png;base64," + readFileSync(inp).toString("base64");
for (const w of widths.split(",").map(Number)) {
  const r = await send("Runtime.evaluate", { awaitPromise: true, returnByValue: true, expression: `(async () => {
    const img = new Image(); img.src = ${JSON.stringify(src)}; await img.decode();
    const c = document.createElement('canvas'); c.width = ${w}; c.height = Math.round(img.naturalHeight * ${w} / img.naturalWidth);
    const x = c.getContext('2d'); x.imageSmoothingQuality = 'high'; x.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL('image/webp', ${Number(q)});
  })()` });
  const out = `${prefix}-${w}.webp`;
  writeFileSync(out, Buffer.from(r.result.result.value.split(",")[1], "base64"));
  console.log("wrote", out);
}
s.close(); edge.kill(); process.exit(0);
