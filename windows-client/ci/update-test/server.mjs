// TEST ONLY. The CI update test's stand-in for https://pricechat.live/downloads/kiwi/windows/: serves files from a folder over
// HTTPS on port 443 with a throwaway certificate for kiwi-update.test (trusted only on the CI runner). A file "<path>.redirect"
// makes that path answer 302 to the URL inside it (the service must refuse to follow).
//   node server.mjs <root folder> <server.pfx> <pfx password> [port]
import fs from "node:fs";
import https from "node:https";
import path from "node:path";

const [rootArg, pfx, passphrase, port = "443"] = process.argv.slice(2);
const root = path.resolve(rootArg);
const log = path.join(root, "requests.log");

https
  .createServer({ pfx: fs.readFileSync(pfx), passphrase }, (req, res) => {
    const url = new URL(req.url ?? "/", "https://kiwi-update.test");
    fs.appendFileSync(log, `${new Date().toISOString()} ${req.method} ${url.pathname}\n`);
    const file = path.join(root, path.normalize(decodeURIComponent(url.pathname)).replace(/^([/\\])+/, ""));
    if (!file.startsWith(root)) { res.writeHead(400).end(); return; }
    if (fs.existsSync(`${file}.redirect`)) {
      res.writeHead(302, { Location: fs.readFileSync(`${file}.redirect`, "utf8").trim() }).end();
      return;
    }
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404).end(); return; }
    const body = fs.readFileSync(file);
    res.writeHead(200, { "Content-Type": file.endsWith(".json") ? "application/json" : "application/octet-stream", "Content-Length": body.length, "Cache-Control": "no-store" });
    res.end(body);
  })
  .listen(Number(port), "127.0.0.1", () => console.log(`serving ${root} on https://kiwi-update.test:${port}`));
