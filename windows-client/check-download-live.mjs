// LIVE check, changing nothing: the public download's bytes match the expected SHA-256, and /vpn really offers it with its Public beta notes. It only hashes the download and never runs it. Usage: node check-download-live.mjs <sha256>
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WANT = (process.argv[2] || "").toUpperCase();
const SITE = "https://pricechat.live", URL_ = `${SITE}/downloads/KiwiVPN-Setup.exe`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (name, ok, detail = "") => { if (!ok) failed++; console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  [" + detail + "]" : ""}`); };

const res = await fetch(`${URL_}?v=${Date.now()}`);
const bytes = Buffer.from(await res.arrayBuffer());
const got = createHash("sha256").update(bytes).digest("hex").toUpperCase();
check("the download answers 200", res.status === 200, String(res.status));
check("it's a program, not the website's page", bytes[0] === 0x4d && bytes[1] === 0x5a && !String(res.headers.get("content-type")).includes("text/html"), String(res.headers.get("content-type")));
check("its SHA-256 matches", got === WANT, got);
console.log(`      ${bytes.length} bytes, cache-control: ${res.headers.get("cache-control")}`);

const dbg = 9750 + Math.floor(Math.random() * 40);
const proc = spawn("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", ["--headless=new", `--remote-debugging-port=${dbg}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "kiwi-dl-"))}`, "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
let list;
for (let i = 0; i < 80 && !list; i++) { try { list = await (await fetch(`http://127.0.0.1:${dbg}/json/list`)).json(); } catch { await sleep(250); } }
const ws = new WebSocket(list.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const wait = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m.result); wait.delete(m.id); } };
const send = (method, params = {}) => new Promise((res2) => { const n = ++id; wait.set(n, res2); ws.send(JSON.stringify({ id: n, method, params })); });
await send("Page.enable"); await send("Runtime.enable");
await send("Network.setUserAgentOverride", { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0" });
await send("Page.navigate", { url: `${SITE}/vpn?v=${Date.now()}` });
await sleep(6000);
const page = JSON.parse((await send("Runtime.evaluate", { returnByValue: true, expression: `JSON.stringify((()=>{
  const links=[...document.querySelectorAll('a.vpn-windows-go')];
  const card=[...document.querySelectorAll('.vpn-app')].find(a=>a.querySelector('h3')?.textContent.includes('Windows'));
  const notes=card?.querySelector('.vpn-checksum');
  return {hrefs:links.map(a=>a.getAttribute('href')),download:links.every(a=>a.hasAttribute('download')),hero:!!document.querySelector('.vpn-hero a.vpn-windows-go'),heroText:document.querySelector('.vpn-hero a.vpn-windows-go')?.textContent,
    status:card?.querySelector('.vpn-status')?.textContent,notes:notes?.textContent||'',faq:document.querySelector('.vpn')?.textContent.includes('available as a public beta')};
})())` })).result.value);
check("/vpn offers Download for Windows, in the hero and the card", page.hrefs.length === 2 && page.hrefs.every((h) => h === "/downloads/KiwiVPN-Setup.exe") && page.download && page.hero, page.hrefs.join(", "));
check("the hero's button says Public beta", /Public beta/.test(page.heroText || ""), page.heroText);
check("the card says Public beta", page.status === "Public beta", page.status);
check("signed publisher is named", /code signed \(publisher: Avigad Saadia\)/.test(page.notes));
check("the kill switch is described as optional", /Optional kill switch, in Settings/.test(page.notes) && /off unless you turn it on/.test(page.notes));
check("the 0.1 upgrade path is stated", /upgrades it in place/.test(page.notes));
check("it's still called a beta", /still a beta/.test(page.notes));
check("no stale unsigned / no-kill-switch text", !/isn't code signed|no kill switch/.test(page.notes));
check("no passed-testing claim", !/passed|audited|verified/i.test(page.notes));
check("the page shows the same SHA-256", page.notes.includes(WANT));
proc.kill();
console.log(failed ? `${failed} FAILED` : "ALL PASSED");
process.exit(failed ? 1 : 0);
