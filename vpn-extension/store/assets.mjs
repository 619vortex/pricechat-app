// The store images, drawn in a headless browser at their exact sizes. Screenshots frame real popup captures from the e2e run.
//   node vpn-extension/store/assets.mjs <browser exe> <folder of e2e screenshots>
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const [browser, shots] = process.argv.slice(2);
const OUT = join(dirname(fileURLToPath(import.meta.url)), "assets");
mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The Kiwi mark as the toolbar icon and the /vpn page draw it: the brand's black kiwi (frontend/public/brand/kiwi.webp) in a lime circle.
const KIWI = `data:image/webp;base64,${readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend", "public", "brand", "kiwi.webp")).toString("base64")}`;
const MARK = (size) => `<span class="mark" style="display:grid;place-items:center;flex:none;width:${size}px;height:${size}px;border-radius:50%;background:#a3e635"><img src="${KIWI}" alt="" style="width:${Math.round(size * .7)}px;height:auto;transform:translate(-1%,2%)"></span>`;
const BASE = `*{box-sizing:border-box;margin:0}html,body{width:100%;height:100%}body{background:radial-gradient(120% 120% at 15% 0%,#12281c 0%,#070b09 58%);color:#f6f8f4;font-family:"Segoe UI",system-ui,sans-serif;display:flex;align-items:center;overflow:hidden}h1{font-weight:900;letter-spacing:-.02em;line-height:1.04}p{color:#bcc7bf;line-height:1.35}h1 em{font-style:normal;color:#a3e635}`;
const png = (name) => `data:image/png;base64,${readFileSync(join(shots, name)).toString("base64")}`;
const shot = (title, text, file) => ({ width: 1280, height: 800, html: `<style>${BASE}body{padding:0 96px;gap:72px;justify-content:space-between}.t{flex:1}h1{font-size:62px;margin:24px 0 20px}p{font-size:27px}.t .mark{display:grid!important}.t>img,body>img{height:700px;border-radius:18px;border:1px solid rgba(214,245,170,.11);box-shadow:0 30px 80px rgb(0 0 0 / .6)}</style><div class="t">${MARK(58)}<h1>${title}</h1><p>${text}</p></div><img src="${png(file)}">` });

const ASSETS = {
  "logo-300x300.png": { width: 300, height: 300, html: `<style>${BASE}body{background:#070b09;justify-content:center}</style>${MARK(200)}` },
  "promo-small-440x280.png": { width: 440, height: 280, html: `<style>${BASE}body{padding:0 34px;gap:24px}h1{font-size:46px}p{font-size:19px;margin-top:8px}</style>${MARK(90)}<div><h1>Kiwi VPN</h1><p>One switch for private browsing.</p></div>` },
  "promo-marquee-1400x560.png": { width: 1400, height: 560, html: `<style>${BASE}body{padding:0 130px;gap:80px}h1{font-size:110px}p{font-size:36px;margin-top:18px}</style>${MARK(240)}<div><h1>Kiwi VPN</h1><p>One switch sends your browser's traffic through an encrypted connection.</p></div>` },
  "screenshot-1-connected-1280x800.png": shot("One switch", "Press it and your browser's traffic goes through an encrypted connection.", "3-on.png"),
  "screenshot-2-choose-1280x800.png": shot("Clear status", "You can always see whether you're connected, and which server you're using.", "2-off.png"),
  "screenshot-3-problem-1280x800.png": shot("Stops if it drops", "If the connection is lost, traffic stops instead of quietly going around the VPN, until it's back or you switch off.", "4-problem.png"),
  "screenshot-4-sign-in-1280x800.png": shot("Your Kiwi Passport", "Sign in on pricechat.live. The extension covers this browser only; the Kiwi VPN apps cover your whole device.", "1-signed-out.png"),
};

const port = 9500 + Math.floor(Math.random() * 90);
const proc = spawn(browser, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "kiwi-assets-"))}`, "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
for (let i = 0; i < 80; i++) { try { await fetch(`http://127.0.0.1:${port}/json/list`); break; } catch { await sleep(250); } }
for (const [name, a] of Object.entries(ASSETS)) {
  const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl), wait = new Map();
  let n = 0;
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } };
  await new Promise((r) => { ws.onopen = r; });
  const send = (method, params = {}) => { const id = ++n; return new Promise((r) => { wait.set(id, r); ws.send(JSON.stringify({ id, method, params })); }); };
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: a.width, height: a.height, deviceScaleFactor: 1, mobile: false });
  const { result } = await send("Page.getFrameTree");
  await send("Page.setDocumentContent", { frameId: result.frameTree.frame.id, html: `<!doctype html><meta charset="utf-8">${a.html}` });
  await sleep(700);
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(OUT, name), Buffer.from(r.result.data, "base64"));
  console.log(name);
  ws.close();
}
spawnSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"]);
process.exit(0);
