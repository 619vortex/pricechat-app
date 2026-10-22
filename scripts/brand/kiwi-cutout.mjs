// Lifts the official Kiwi off its green avatar background. The Kiwi's own pixels are copied unchanged; only the
// background becomes transparent and the thin anti-aliased edge is un-mixed from the green. Runs in headless Edge.
// Usage: node scripts/brand/kiwi-cutout.mjs <avatar.jpg> frontend/public/brand/kiwi.webp cut 1000 image/webp
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [inp, out, mode, widthArg] = process.argv.slice(2);
const OUT_W = Number(widthArg) || 0; // 0 = keep the trimmed size
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const port = 9334;
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "pc-cut-"))}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) { await sleep(200); try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch {} }
const sock = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => sock.addEventListener("open", r));
let id = 0; const pending = new Map();
sock.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); sock.send(JSON.stringify({ id: i, method, params })); });
const evaluate = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }); if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails)); return r.result?.result?.value; };

const dataUrl = "data:image/jpeg;base64," + readFileSync(inp).toString("base64");
const result = await evaluate(`(async () => {
  const img = new Image(); img.src = ${JSON.stringify(dataUrl)}; await img.decode();
  const W = img.naturalWidth, H = img.naturalHeight;
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
  const d = ctx.getImageData(0, 0, W, H); const p = d.data;
  const chroma = (i) => p[i+1] - Math.max(p[i], p[i+2]);           // how green a pixel is
  if (${JSON.stringify(mode)} === 'sample') {
    const at = (x, y) => { const i = (y*W + x)*4; return [p[i], p[i+1], p[i+2], chroma(i)]; };
    return { W, H, corners: [at(5,5), at(W-6,5), at(5,H-6), at(W-6,H-6)], center: at(W/2|0, H/2|0), eye: at(Math.round(W*0.675), Math.round(H*0.335)) };
  }
  // 1. background = everything green that is connected to the image border (the green iris is enclosed by the outline)
  const BG = 1, EDGE = 2; const mark = new Uint8Array(W*H); const q = new Int32Array(W*H); let qh = 0, qt = 0;
  const isBg = (k) => chroma(k*4) >= 8 && p[k*4+1] >= 16;
  for (let x = 0; x < W; x++) for (const y of [0, H-1]) { const k = y*W+x; if (!mark[k] && isBg(k)) { mark[k] = BG; q[qt++] = k; } }
  for (let y = 0; y < H; y++) for (const x of [0, W-1]) { const k = y*W+x; if (!mark[k] && isBg(k)) { mark[k] = BG; q[qt++] = k; } }
  while (qh < qt) { const k = q[qh++]; const x = k % W, y = (k / W) | 0;
    for (const [dx, dy] of [[1,0],[-1,0],[0,1],[0,-1]]) { const nx = x+dx, ny = y+dy; if (nx<0||ny<0||nx>=W||ny>=H) continue; const n = ny*W+nx; if (!mark[n] && isBg(n)) { mark[n] = BG; q[qt++] = n; } } }
  // 2. the anti-aliased band (up to R px into the Kiwi): carry the nearest background colour inward, then un-mix
  const R = Math.max(2, Math.round(W / 480 * 2.5));
  const src = new Int32Array(W*H).fill(-1); const dist = new Uint8Array(W*H).fill(255);
  qh = qt = 0;
  for (let k = 0; k < W*H; k++) if (mark[k] === BG) { src[k] = k; dist[k] = 0; q[qt++] = k; }
  while (qh < qt) { const k = q[qh++]; if (dist[k] >= R) continue; const x = k % W, y = (k / W) | 0;
    for (const [dx, dy] of [[1,0],[-1,0],[0,1],[0,-1]]) { const nx = x+dx, ny = y+dy; if (nx<0||ny<0||nx>=W||ny>=H) continue; const n = ny*W+nx;
      if (mark[n] !== BG && dist[n] === 255) { dist[n] = dist[k] + 1; src[n] = src[k]; mark[n] = EDGE; q[qt++] = n; } } }
  let edgeCount = 0, bgCount = 0;
  for (let k = 0; k < W*H; k++) { const i = k*4;
    if (mark[k] === BG) { p[i+3] = 0; bgCount++; continue; }
    if (mark[k] !== EDGE) continue;                                  // Kiwi interior: untouched
    const b = src[k]*4; const cb = chroma(b), cp = chroma(i);
    // the Kiwi's edge is its black outline: alpha is how much of the background's green remains
    let a = cb > 0 ? 1 - Math.max(0, cp) / cb : 1; a = Math.min(1, Math.max(0, a));
    if (a < 0.03) { p[i+3] = 0; continue; }
    for (let ch = 0; ch < 3; ch++) p[i+ch] = Math.round(Math.min(255, Math.max(0, (p[i+ch] - (1-a) * p[b+ch]) / a)));
    p[i+3] = Math.round(a * 255); edgeCount++;
  }
  // 3. keep only the Kiwi: the largest connected opaque shape (drops stray JPEG specks in the dark corner)
  const comp = new Int32Array(W*H).fill(-1); let best = -1, bestSize = 0, cid = 0;
  for (let s = 0; s < W*H; s++) { if (comp[s] !== -1 || p[s*4+3] === 0) continue;
    qh = qt = 0; q[qt++] = s; comp[s] = cid;
    while (qh < qt) { const k = q[qh++]; const x = k % W, y = (k / W) | 0;
      for (const [dx, dy] of [[1,0],[-1,0],[0,1],[0,-1]]) { const nx = x+dx, ny = y+dy; if (nx<0||ny<0||nx>=W||ny>=H) continue; const n = ny*W+nx;
        if (comp[n] === -1 && p[n*4+3] > 0) { comp[n] = cid; q[qt++] = n; } } }
    if (qt > bestSize) { bestSize = qt; best = cid; } cid++; }
  let removed = 0;
  for (let k = 0; k < W*H; k++) if (p[k*4+3] > 0 && comp[k] !== best) { p[k*4+3] = 0; removed++; }
  ctx.putImageData(d, 0, 0);
  // trim to the Kiwi's bounding box (+ small margin) so placement in CSS is exact
  let x0 = W, y0 = H, x1 = 0, y1 = 0;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (p[(y*W+x)*4+3] > 8) { if (x<x0) x0=x; if (x>x1) x1=x; if (y<y0) y0=y; if (y>y1) y1=y; }
  const m = Math.round(W * 0.01); x0 = Math.max(0, x0-m); y0 = Math.max(0, y0-m); x1 = Math.min(W-1, x1+m); y1 = Math.min(H-1, y1+m);
  const t = document.createElement('canvas'); t.width = x1-x0+1; t.height = y1-y0+1;
  t.getContext('2d').drawImage(c, x0, y0, t.width, t.height, 0, 0, t.width, t.height);
  let o = t;
  if (${OUT_W} && ${OUT_W} < t.width) { o = document.createElement('canvas'); o.width = ${OUT_W}; o.height = Math.round(t.height * ${OUT_W} / t.width);
    const oc = o.getContext('2d'); oc.imageSmoothingEnabled = true; oc.imageSmoothingQuality = 'high'; oc.drawImage(t, 0, 0, o.width, o.height); }
  return { W, H, bgCount, edgeCount, removed, components: cid, box: [x0, y0, x1, y1], size: [o.width, o.height], png: o.toDataURL(${JSON.stringify(process.argv[6] || "image/png")}, 0.92) };
})()`);
if (mode === "sample") console.log(JSON.stringify(result));
else { writeFileSync(out, Buffer.from(result.png.split(",")[1], "base64")); delete result.png; console.log(JSON.stringify(result)); }
sock.close(); edge.kill(); process.exit(0);
