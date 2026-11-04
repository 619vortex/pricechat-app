import { invoke } from "@tauri-apps/api/core";

/**
 * The window's only knowledge of the VPN is the service's answer. The words on screen come from `phaseOf`, which is the one place "connected" can be decided here.
 * It repeats the service's rule rather than trusting a label: no recent handshake, no "connected", whatever else the answer says.
 */

export type TunnelState = "disconnected" | "connecting" | "connected" | "stopping" | "failed";
export interface Status { state: TunnelState; handshake_age_seconds: number | null; rx_bytes: number; tx_bytes: number; error: string | null }
export interface Answer { service: boolean; status: Status | null; message: string | null }
export type Phase = "loading" | "no-service" | "off" | "connecting" | "on" | "stopping" | "failed";

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
/** Opens a file picker in the app's Rust side; the config never reaches this page. Null if the person cancelled. */
export const vpnConnectTestFile = () => invoke<Answer | null>("vpn_connect_test_file");
export const isTestBuild = () => invoke<boolean>("test_build");

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

export interface Sample { at: number; rx: number; tx: number }

/** Bytes per second between two readings. A counter that went backwards means a new tunnel, so that reading says nothing. */
export function ratesBetween(a: Sample | null, b: Sample): { rx: number; tx: number } {
  const seconds = a ? (b.at - a.at) / 1000 : 0;
  if (!a || seconds <= 0 || b.rx < a.rx || b.tx < a.tx) return { rx: 0, tx: 0 };
  return { rx: (b.rx - a.rx) / seconds, tx: (b.tx - a.tx) / seconds };
}
