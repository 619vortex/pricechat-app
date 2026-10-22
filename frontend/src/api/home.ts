import { request } from "./client";

export interface TodoItem { key: string; label: string; count: number; to: string }
export interface AdminHome {
  todo: TodoItem[];
  status: { kill_switch: boolean; telegram: string; x: string; ai: boolean; last_scan_at: string | null; failing_jobs: string[] };
  numbers: { members: number; new_members_7d: number; signals_7d: number; open_signals: number };
}

export const adminHome = () => request<AdminHome>("/admin/home");
