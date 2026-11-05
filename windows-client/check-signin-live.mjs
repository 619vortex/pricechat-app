// LIVE check, signed out, changing nothing, in headless Edge. A tab that opened the app's sign-in page leaves the site and arrives at /passport the way Google sends it back:
// it should return to the app's page. A fresh tab, with no app involved, should behave as normal. Exits 1 if not.
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const dbg = 9800 + Math.floor(Math.random() * 40);
const proc = spawn("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", ["--headless=new", `--remote-debugging-port=${dbg}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "kiwi-sl-"))}`, "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
let list;
for (let i = 0; i < 80 && !list; i++) { try { list = await (await fetch(`http://127.0.0.1:${dbg}/json/list`)).json(); } catch { await sleep(250); } }

async function tab(url) {
  const ws = new WebSocket(url);
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const wait = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m.result); wait.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res) => { const n = ++id; wait.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
  const value = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true })).result.value;
  await send("Page.enable");
  return { send, go: async (u, ms = 4500) => { await send("Page.navigate", { url: u }); await sleep(ms); }, where: () => value("location.href"), text: () => value("document.querySelector('.vpn')?.innerText||''") };
}

const SITE = "https://pricechat.live";
const Q = `challenge=${"a".repeat(43)}&port=49152&state=${"s".repeat(24)}`;
const PAGE = `${SITE}/vpn/desktop?${Q}`;
let failed = 0;
const check = (name, ok, detail = "") => { if (!ok) failed++; console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  [" + detail.replace(Q, "<app request>") + "]" : ""}`); };

const a = await tab(list.find((t) => t.type === "page").webSocketDebuggerUrl);
await a.go(PAGE);
check("the app's page offers sign in", /Sign in with your Price Chat account/.test(await a.text()));
await a.go("https://example.com/", 2500);
check("the tab left the site", (await a.where()).startsWith("https://example.com"));
await a.go(`${SITE}/passport?welcome=google`);
const back = await a.where();
check("coming back to /passport returns to the app's page", back === PAGE, back);
// Not checked here: that it happens only once. This browser is still signed out, so the app's page remembers the request again, as it should. Once signed in, it forgets it. That needs a session: see vpnDesktop.test.tsx.

const { targetId } = await a.send("Target.createTarget", { url: "about:blank" });
const fresh = (await (await fetch(`http://127.0.0.1:${dbg}/json/list`)).json()).find((t) => t.id === targetId);
const b = await tab(fresh.webSocketDebuggerUrl);
await b.go(`${SITE}/passport?welcome=google`);
const normal = await b.where();
check("a fresh tab with no app involved isn't sent there", !normal.includes("/vpn/desktop"), normal);

proc.kill();
process.exit(failed ? 1 : 0);
