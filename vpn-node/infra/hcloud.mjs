// A small Hetzner Cloud API client. The token is read once, kept in memory, sent only to api.hetzner.cloud, never printed.
import { execFileSync } from "node:child_process";

const API = "https://api.hetzner.cloud/v1";

/** HCLOUD_TOKEN from this process, or (Windows) from the user's environment when this process started before it was set. */
export function loadToken(env = process.env, platform = process.platform, readUserEnv = readWindowsUserEnv) {
  let t = (env.HCLOUD_TOKEN || "").trim();
  if (t.length < 64 && platform === "win32") t = (readUserEnv("HCLOUD_TOKEN") || "").trim();
  if (t.length < 64) throw new Error("no Hetzner Cloud token: set HCLOUD_TOKEN (a 64-character API token from the Hetzner console > Security > API tokens)");
  return t;
}

function readWindowsUserEnv(name) {
  return execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
    `[Environment]::GetEnvironmentVariable('${name}','User')`], { encoding: "utf8", windowsHide: true });
}

export class HCloud {
  #token;
  constructor(token, fetchImpl = fetch) { this.#token = token; this.fetch = fetchImpl; }

  async call(method, path, body) {
    const res = await this.fetch(API + path, {
      method,
      headers: { Authorization: `Bearer ${this.#token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`Hetzner ${method} ${path.split("?")[0]}: ${res.status} ${data.error?.code ?? ""} ${data.error?.message ?? ""}`.trim());
    return data;
  }

  /** Every page of a list endpoint. */
  async list(path, key) {
    const out = [];
    for (let page = 1; page < 50; page++) {
      const sep = path.includes("?") ? "&" : "?";
      const d = await this.call("GET", `${path}${sep}per_page=50&page=${page}`);
      out.push(...d[key]);
      if (!d.meta?.pagination?.next_page) break;
    }
    return out;
  }

  /** Everything a plan or an audit needs, read-only. */
  async snapshot() {
    const [servers, firewalls, primaryIps, sshKeys, serverTypes, volumes, snapshots, pricing] = await Promise.all([
      this.list("/servers", "servers"), this.list("/firewalls", "firewalls"), this.list("/primary_ips", "primary_ips"),
      this.list("/ssh_keys", "ssh_keys"), this.list("/server_types", "server_types"), this.list("/volumes", "volumes"),
      this.list("/images?type=snapshot", "images"), this.call("GET", "/pricing").then((d) => d.pricing),
    ]);
    return { servers, firewalls, primaryIps, sshKeys, serverTypes, volumes, snapshots, pricing };
  }

  async waitAction(action, { sleep = (ms) => new Promise((r) => setTimeout(r, ms)), tries = 120 } = {}) {
    for (let i = 0; i < tries; i++) {
      const a = (await this.call("GET", `/actions/${action.id}`)).action;
      if (a.status === "success") return a;
      if (a.status === "error") throw new Error(`Hetzner action ${a.command} failed: ${a.error?.message ?? ""}`);
      await sleep(2000);
    }
    throw new Error(`Hetzner action ${action.command} is still running`);
  }
}
