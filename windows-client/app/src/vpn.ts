import { invoke } from "@tauri-apps/api/core";

/**
 * The window's only knowledge of the VPN is the service's answer. The words on screen come from `phaseOf`, which is the one place "connected" can be decided here.
 * It repeats the service's rule rather than trusting a label: no recent handshake, no "connected", whatever else the answer says.
 * Account calls run in the app's Rust side: the session token and private keys never reach this page.
 */

export type TunnelState = "disconnected" | "connecting" | "connected" | "stopping" | "failed";
export interface Status { state: TunnelState; handshake_age_seconds: number | null; rx_bytes: number; tx_bytes: number; error: string | null; connected_since: number | null }
export interface Answer { service: boolean; status: Status | null; message: string | null }
export type Phase = "loading" | "no-service" | "off" | "connecting" | "on" | "stopping" | "failed";

export interface Server { id: number; name: string; region: string; country_code: string; online: boolean; recommended: boolean }
export interface Account { enabled: boolean; eligible: boolean; reason: string | null; max_devices: number; servers: Server[]; devices: { id: number; name: string; server_id: number }[];
  plan: { kind: string; days_left: number | null } | null; trial: { available: boolean; days: number } | null }
export interface AccountView { signed_in: boolean; account: Account | null; suggested: number | null; last_server: number | null; error: string | null }

/** The same limit as HANDSHAKE_FRESH_SECONDS in the service's protocol.rs. WireGuard rekeys about every two minutes. */
export const HANDSHAKE_FRESH_SECONDS = 180;

export function phaseOf(answer: Answer | null): Phase {
  if (!answer) return "loading";
  if (!answer.service || !answer.status) return "no-service";
  const { state, handshake_age_seconds: age } = answer.status;
  if (state === "connected") return age !== null && age <= HANDSHAKE_FRESH_SECONDS ? "on" : "connecting";
  if (state === "connecting") return "connecting";
  if (state === "stopping") return "stopping";
  if (state === "failed") return "failed";
  return "off";
}

export const vpnStatus = () => invoke<Answer>("vpn_status");
export const vpnDisconnect = () => invoke<Answer>("vpn_disconnect");
export const vpnConnect = (serverId: number) => invoke<Answer>("vpn_connect", { serverId });
export const vpnConnectTestFile = () => invoke<Answer | null>("vpn_connect_test_file");
export const hasTestConfig = () => invoke<boolean>("has_test_config");
export const accountState = () => invoke<AccountView>("account_state");
export const signIn = () => invoke<void>("sign_in");
export const cancelSignIn = () => invoke<void>("cancel_sign_in");
export const signOut = () => invoke<void>("sign_out");
export const startTrial = () => invoke<void>("start_trial");
export const myIp = () => invoke<string>("my_ip");
export const openSite = (page: "plans" | "devices") => invoke<void>("open_site", { page });

/** "Falkenstein, Germany" from whatever Admin typed: "Germany" + "Falkenstein, Germany", or "Helsinki 1" + "Finland". */
export function serverPlace(s: Pick<Server, "name" | "region" | "country_code">): string {
  const parts = (s.region || "").split(",").map((p) => p.trim()).filter(Boolean);
  if (parts.length > 1) return `${parts[0]}, ${parts[parts.length - 1]}`;
  const city = (s.name || "").replace(/\s*#?\d+$/, "").trim();
  const country = parts[0] || countryName(s.country_code);
  return city && city !== country ? `${city}, ${country}` : country;
}

export function countryName(code: string): string {
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of((code || "").toUpperCase()) ?? code; } catch { return code; }
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n, u = -1;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[u]}`;
}

export function formatRate(bytesPerSecond: number): string {
  return `${formatBytes(Math.round(bytesPerSecond))}/s`;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
}

/** Seconds the tunnel has been up, from the service's own record, so it's the same whether or not this window was open. Null when the service doesn't give one. */
export function secondsUp(since: number | null | undefined, nowMs: number): number | null {
  return since === null || since === undefined ? null : Math.max(0, nowMs / 1000 - since);
}

export interface Sample { at: number; rx: number; tx: number }

/** Bytes per second between two readings. A counter that went backwards means a new tunnel, so that reading says nothing. */
export function ratesBetween(a: Sample | null, b: Sample): { rx: number; tx: number } {
  const seconds = a ? (b.at - a.at) / 1000 : 0;
  if (!a || seconds <= 0 || b.rx < a.rx || b.tx < a.tx) return { rx: 0, tx: 0 };
  return { rx: (b.rx - a.rx) / seconds, tx: (b.tx - a.tx) / seconds };
}
