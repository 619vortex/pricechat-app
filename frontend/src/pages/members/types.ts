import type { Rank, XPEvent } from "../../website/members";

export interface MembersOverview {
  totals: { members: number; active_7d: number; new_7d: number; banned: number; xp_suspended: number; xp_today: number; open_flags: number };
  linked: { telegram: number; x: number; google: number };
  rank_distribution: { level: number; name: string; members: number }[];
  members_over_time: { day: string; members: number }[];
  active_per_day: { day: string; members: number }[];
  xp_by_source: { source: string; xp: number }[];
}

export interface MemberRow {
  id: string;
  username: string;
  display_name: string;
  xp: number;
  bonus_xp: number;
  rank_level: number;
  rank: string;
  linked: string[];
  last_active_at: string | null;
  joined_at: string;
  status: "active" | "banned";
  xp_suspended: boolean;
  open_flags: number;
  roles?: ("WORKER" | "MANAGER")[];
  leaderboard_opt_out: boolean;
}

export interface MemberDetail {
  member: MemberRow;
  rank: { current: Rank; next: Rank | null; xp_to_next: number; progress: number };
  identities: { platform: string; username: string | null; display_name: string | null; platform_user_id: string; verified_at: string; status: string }[];
  // admin only: the member's X connection (never the tokens) and Telegram statistics
  x_connection?: { x_user_id: string; scopes: string[]; status: string; last_error: string | null; expires_at: string | null;
                   has_refresh_token: boolean; connected_at: string; last_used_at: string | null } | null;
  telegram?: { messages_today: number; eligible_today: number; xp_today: number; daily_cap: number; messages_7d?: number; xp_7d?: number;
               active_days_7d: number; last_verified_at: string | null } | null;
  badges: { key: string; name: string; description: string; awarded_at: string; awarded_by: string }[];
  entitlements: { key: string; name: string; required_rank: number | null; enabled: boolean; unlocked: boolean }[];
  flags: { id: number; kind: string; reason: string; status: string; created_by: string; created_at: string; resolution: string | null;
           details?: Record<string, unknown> }[];
  events: (XPEvent & { actor: string; source_ref: string })[];
  audit: { actor: string; action: string; details: Record<string, unknown>; at: string }[];
  reputation?: { formula: string; price_chat_xp: number; price_chat_points: number; legacy_points: number; legacy_raw: number;
                 capped: boolean; rank_score: number; combot_counts: boolean; combot: { xp: number; messages: number | null; rep: number | null;
                 imported_at: string; source_type: string } | null };
}

export interface RuleRow {
  event_type: string;
  label: string;
  description: string;
  xp: number;
  daily_cap: number | null;
  cooldown_seconds: number;
  max_per_member: number | null;
  min_member_age_hours: number;
  enabled: boolean;
  availability: "available" | "unavailable" | "future";
  availability_note: string;
  verification_source: string;
  category: string;
}

export interface MembersConfig {
  rules: RuleRow[];
  ranks: Rank[];
  badges: { key: string; name: string; description: string; kind: string; enabled: boolean; criteria: Record<string, unknown> }[];
  entitlements: { key: string; name: string; description: string; required_rank: number | null; enabled: boolean }[];
  daily_xp_cap: number;
}

export interface MissionRow {
  daily_cap?: number | null; // of the rule that pays it (or the overall mission cap)
  pays_xp?: boolean;
  no_xp_reason?: string | null;
  id: number;
  key: string;
  kind: string;
  cadence: string;
  title: string;
  description: string;
  platform: string;
  xp: number;
  xp_from_rule: boolean;
  verification: string;
  verification_label: string;
  verification_method: string;
  status: "active" | "paused" | "disabled" | "expired";
  goal: number;
  target: { url?: string; text?: string; title?: string };
  created_by: string;
  expires_at: string | null;
  created_at: string;
  completed: number;
  xp_issued: number;
  pending: number;
  rejected: number;
}

export interface ReputationSources {
  settings: { price_chat_weight: number; combot_enabled: boolean; combot_weight: number; legacy_cap: number; telegram_daily_cap: number; watch_seconds?: number;
              combot_mode: "api" | "manual" | "off"; combot_chat_id: string | null };
  price_chat_telegram: { status: string; linked_members: number; xp_events: number };
  combot: { mode: string; api_key: boolean; status: string; records: number; linked_members: number; matched: number; unmatched: number;
            last_sync_at: string | null; last_success_at: string | null; last_error: string | null; last_records: number };
  imports: { id: number; source_type: string; status: string; filename: string | null; stats: Record<string, unknown>;
             created_at: string; applied_at: string | null; created_by: string }[];
  formula: string;
}

export interface AuditEntry {
  id: number;
  member: string | null;
  actor: string;
  action: string;
  details: Record<string, unknown>;
  at: string;
}
