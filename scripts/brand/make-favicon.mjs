// Builds frontend/public/favicon.png FROM the canonical Kiwi (frontend/public/brand/kiwi.webp): the same image on a
// Price Chat green circle, like the website's logo mark. Never draw the Kiwi by hand; re-run this after replacing it.
// Uses headless Microsoft Edge (canvas), so no image libraries are needed. Usage: node scripts/brand/make-favicon.mjs
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const KIWI = join(root, "frontend", "public", "brand", "kiwi.webp");
const OUT = join(root, "frontend", "public", "favicon.png");
const SIZE = 128;
const EDGE = process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

const port = 9335;
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "pc-fav-"))}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) { await sleep(200); try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch {} }
const sock = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => sock.addEventListener("open", r));
const pending = new Map(); let id = 0;
sock.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
const send = (method, params) => new Promise((r) => { const i = ++id; pending.set(i, r); sock.send(JSON.stringify({ id: i, method, params })); });

const kiwi = "data:image/webp;base64," + readFileSync(KIWI).toString("base64");
const res = await send("Runtime.evaluate", { awaitPromise: true, returnByValue: true, expression: `(async () => {
  const img = new Image(); img.src = ${JSON.stringify(kiwi)}; await img.decode();
  const S = ${SIZE}; const c = document.createElement('canvas'); c.width = c.height = S; const x = c.getContext('2d');
  x.fillStyle = '#00e58f'; x.beginPath(); x.arc(S/2, S/2, S/2, 0, Math.PI*2); x.fill();
  // same placement as the logo mark: 80% wide, nudged slightly left/down to optically centre the body
  const w = S * 0.8, h = w * img.naturalHeight / img.naturalWidth;
  x.imageSmoothingQuality = 'high';
  x.drawImage(img, (S - w) / 2 - S * 0.016, (S - h) / 2 + S * 0.024, w, h);
  return c.toDataURL('image/png');
})()` });
writeFileSync(OUT, Buffer.from(res.result.result.value.split(",")[1], "base64"));
console.log(`wrote ${OUT}`);
sock.close(); edge.kill(); process.exit(0);
