// Mirrors backend/app/api/schemas.py

export type AgentStatus = "idle" | "active" | "paused" | "error";
export type MemoryLayer = "conversation" | "brand" | "community" | "social";
export type MemoryKind =
  | "text"
  | "note"
  | "brand_fact"
  | "community_insight"
  | "message"
  | "post"
  | "document"
  | "summary";
export type SourceType = "manual" | "chat" | "agent" | "telegram" | "x" | "document" | "url" | "system";
export type AgentTask = "chat" | "draft_post" | "draft_reply" | "summarize" | "model_test";
export type PromptPlatform = "dashboard" | "x" | "telegram" | "youtube" | "youtube_shorts" | "tiktok";
export type ContentStatus = "draft" | "queued" | "pending_approval" | "approved" | "published" | "rejected";
export type Platform = "x" | "telegram";
export type ConnectionStatus = "not_connected" | "connected" | "error" | "disabled";
export type ActivityLevel = "info" | "warning" | "error" | "audit";
export type ActivityCategory = "ai" | "memory" | "community" | "social" | "task" | "config" | "safety" | "content" | "system";
export type ActionMode = "approval" | "automatic";
export type SocialAction = "post" | "reply" | "like" | "repost" | "follow" | "direct_message";
export type ModelState = "online" | "model_missing" | "offline";

export interface ProviderStatus {
  provider: string;
  model: string;
  available: boolean;
  model_loaded: boolean;
  detail: string | null;
  models: string[];
  endpoint: string | null;
  latency_ms: number | null;
}

export interface ModelStatus {
  provider: string;
  model: string;
  default_model: string;
  model_overridden: boolean;
  temperature: number;
  status: ModelState;
  endpoint: string | null;
  detail: string | null;
  health_latency_ms: number | null;
  installed_models: string[];
  last_success_at: string | null;
  last_success_model: string | null;
  last_inference_latency_ms: number | null;
  last_error: string | null;
  embedding_provider: string | null;
  embedding_model: string | null;
}

export interface ModelTestResult {
  ok: boolean;
  reply: string;
  model: string;
  provider: string;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  tokens_per_second: number | null;
}

export interface AgentConfig {
  id: number;
  slug: string;
  name: string;
  identity: string;
  behavior_rules: string[];
  status: AgentStatus;
  model_name: string | null;
  temperature: number | null;
  updated_at: string;
}

export interface Goal {
  id: number;
  name: string;
  description: string;
  priority: number;
  enabled: boolean;
  updated_at: string;
}

export interface MemoryStats {
  total: number;
  by_layer: Record<string, number>;
  by_kind: Record<string, number>;
  by_source_type: Record<string, number>;
  embedded: number;
  embedding_model: string | null;
  conversations: number;
}

export interface AgentBrain {
  config: AgentConfig;
  effective_model: string;
  active_goals: Goal[];
  memory: MemoryStats;
  brand_completeness: number;
  available_providers: string[];
}

export const BRAND_TEXT_FIELDS = ["brand_name", "description", "mission", "audience", "tone", "writing_style", "personality"] as const;
export const BRAND_LIST_FIELDS = [
  "products_services",
  "topics_of_interest",
  "topics_to_avoid",
  "terminology",
  "allowed_claims",
  "restricted_claims",
  "social_objectives",
  "x_voice",
  "x_avoid",
] as const;
export type BrandTextField = (typeof BRAND_TEXT_FIELDS)[number];
export type BrandListField = (typeof BRAND_LIST_FIELDS)[number];
export type BrandProfile = Record<BrandTextField, string> & Record<BrandListField, string[]> & { updated_at: string };

export interface Brand {
  profile: BrandProfile;
  completeness: number;
  empty_fields: string[];
}

export interface Memory {
  id: number;
  layer: MemoryLayer;
  kind: MemoryKind;
  title: string | null;
  content: string;
  tags: string[];
  source_type: SourceType;
  source: string | null;
  external_id: string | null;
  platform: Platform | null;
  author: string | null;
  importance: number;
  occurred_at: string | null;
  created_at: string;
  updated_at: string;
  review_status: ReviewStatus;
  extra: Record<string, unknown> & {
    insight_type?: string;
    confidence?: number;
    generated_by?: { provider: string; model: string; task: string };
    generated_at?: string;
    summary_id?: number;
  };
}

export interface RetrievedMemory {
  memory: Memory;
  score: number;
  semantic: number | null;
  lexical: number;
  reasons: string[];
}

export interface Retrieval {
  strategy: string;
  duration_ms: number;
  notes: string[];
  memories: RetrievedMemory[];
}

export interface ChatReply {
  reply: string;
  conversation_id: string;
  message_id: number;
  model: string;
  provider: string;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  tokens_per_second: number | null;
  retrieval: Retrieval;
  prompt_chars: number;
}

export interface ConversationMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  task: AgentTask;
  platform: PromptPlatform;
  model: string | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  memory_ids: number[];
  created_at: string;
}

export interface CompiledPrompt {
  text: string;
  chars: number;
  approx_tokens: number;
  sections: { key: string; title: string | null; chars: number }[];
  retrieval: Retrieval | null;
}

export interface KnowledgeSaved {
  memory: Memory;
  created: boolean;
  indexed: boolean;
}

export interface KnowledgeSearch {
  results: RetrievedMemory[];
  strategy: string;
  duration_ms: number;
  notes: string[];
}

export interface KnowledgeSource {
  key: string;
  display_name: string;
  description: string;
  source_type: SourceType;
  layer: MemoryLayer;
  available: boolean;
}

export interface Content {
  id: number;
  title: string | null;
  body: string;
  status: ContentStatus;
  target_platform: Platform | null;
  scheduled_for: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  draft_type?: DraftType | null;
  opportunity_id?: number | null;
  variant_group?: string | null;
  variant_label?: string | null;
  context?: Record<string, unknown>;
}

export interface Connector {
  platform: Platform;
  display_name: string;
  status: ConnectionStatus;
  detail: string;
  planned_capabilities: string[];
  enabled_capabilities: string[];
}

export interface Activity {
  id: number;
  level: ActivityLevel;
  event: string;
  category: ActivityCategory;
  label: string;
  message: string;
  actor: string;
  platform: Platform | null;
  action: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Overview {
  agent_name: string;
  agent_status: AgentStatus;
  llm: ProviderStatus;
  effective_model: string;
  last_success_at: string | null;
  connectors: Connector[];
  social_accounts_connected: number;
  tasks: { pending: number; running: number; failed: number };
  memory_count: number;
  active_goals: Goal[];
  kill_switch: boolean;
  recent_activity: Activity[];
  queued_content: Content[];
}

export interface SafetyPolicy {
  id: number;
  mode: ActionMode;
  kill_switch: boolean;
  outbound_enabled: boolean;
  max_actions_per_hour: number;
  max_actions_per_day: number;
  updated_at: string;
}

export interface ActionLimit {
  id: number;
  platform: Platform;
  action: SocialAction;
  enabled: boolean;
  mode: ActionMode | null;
  max_per_hour: number;
  max_per_day: number;
}

export interface SafetySettings {
  policy: SafetyPolicy;
  limits: ActionLimit[];
  outbound_note: string;
}

// --- Phase 3A: Telegram & community intelligence ---------------------------------------

export type ReviewStatus = "approved" | "pending" | "rejected";
export type TopicStatus = "candidate" | "active" | "archived" | "merged";
export type TopicTrend = "new" | "rising" | "steady" | "falling" | "insufficient_data";
export type QuestionStatus = "open" | "answered" | "dismissed";
export type SummaryKind = "recent" | "daily" | "topic";
export type OpportunityStatus = "detected" | "reviewing" | "approved" | "drafted" | "rejected" | "published";
export type ContentChannel = "x" | "youtube" | "youtube_shorts" | "tiktok" | "telegram";
export type MessageCategory = "question" | "feedback" | "concern" | "idea" | "information" | "announcement" | "chatter" | "noise";

export interface TelegramSource {
  id: number;
  chat_id: number;
  display_name: string;
  chat_type: string | null;
  username: string | null;
  enabled: boolean;
  ingestion_enabled: boolean;
  via?: "bot" | "account";
  created_at: string;
  last_sync_at: string | null;
  last_message_at: string | null;
  message_count: number;
  last_error: string | null;
}

export interface DiscoveredChat {
  chat_id: number;
  title: string;
  chat_type: string;
  last_seen_at: string;
  ignored_messages: number;
  bot_status: string | null;
}

export interface TelegramStatus {
  configured: boolean;
  status: "not_configured" | "disconnected" | "connected" | "error";
  bot_username: string | null;
  bot_name: string | null;
  last_sync_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  messages_ingested: number;
  ignored_updates: number;
  sources: TelegramSource[];
  discovered_chats: DiscoveredChat[];
  setup_steps: string[];
}

export interface TelegramMessage {
  id: number;
  source_id: number;
  chat_id: number;
  message_id: number;
  thread_id: number | null;
  reply_to_message_id: number | null;
  author_id: number | null;
  author_name: string | null;
  author_is_bot: boolean;
  sent_at: string;
  edited_at: string | null;
  text: string;
  media_type: string | null;
  forward_from: string | null;
  category: MessageCategory | null;
  is_question: boolean;
  topic_id: number | null;
  question_id: number | null;
  processed_at: string | null;
  created_at: string;
}

export interface DeletionReport {
  messages: number;
  summaries: number;
  memories: number;
  opportunities: number;
}

export interface Topic {
  id: number;
  name: string;
  description: string;
  keywords: string[];
  status: TopicStatus;
  first_seen_at: string;
  last_seen_at: string;
  message_count: number;
  participant_count: number;
  count_24h: number;
  count_prev_24h: number;
  trend: TopicTrend;
  importance: number;
  confidence: number;
  named_by_model: string | null;
  aliases: string[];
  related_ids: number[];
  canonical_id: number | null;
  review_status: string;
  user_importance: number | null;
}

export interface CommunityQuestion {
  id: number;
  text: string;
  example: string;
  status: QuestionStatus;
  ask_count: number;
  asker_count: number;
  first_asked_at: string;
  last_asked_at: string;
  topic_id: number | null;
  faq_memory_id: number | null;
  canonical_model: string | null;
  review_status: string;
  user_importance: number | null;
}

export interface SummaryPoint {
  text: string;
  message_ids: number[];
}

export interface CommunitySummary {
  id: number;
  kind: SummaryKind;
  source_id: number | null;
  topic_id: number | null;
  period_start: string;
  period_end: string;
  headline: string;
  body: string;
  data: Record<string, SummaryPoint[] | string | number> & { overview?: string };
  message_count: number;
  participant_count: number;
  provider: string;
  model: string;
  generated_at: string;
  duration_ms: number | null;
}

export interface Opportunity {
  id: number;
  title: string;
  why: string;
  angle: string;
  evidence: Record<string, unknown>;
  platforms: ContentChannel[];
  urgency: "low" | "medium" | "high";
  confidence: number;
  status: OpportunityStatus;
  origin_kind: string;
  origin_id: number | null;
  content_id: number | null;
  notes: string;
  provider: string | null;
  model: string | null;
  generated_at: string | null;
  reviewed_at: string | null;
  created_at: string;
  signals: Record<string, number>;
  reasons: string[];
  priority: number;
  priority_label: "high" | "medium" | "low" | "";
  ranked_at: string | null;
  content_potential: number | null;
  user_importance: number | null;
}

export interface TopicDetail {
  topic: Topic;
  representative: TelegramMessage[];
  recent: TelegramMessage[];
  questions: CommunityQuestion[];
  memories: Memory[];
  summaries: CommunitySummary[];
  opportunities: Opportunity[];
  volume_by_day: { day: string; messages: number }[];
}

export interface CommunityOverview {
  telegram: TelegramStatus;
  messages_total: number;
  messages_today: number;
  active_members_7d: number;
  active_members_today: number;
  unprocessed: number;
  top_topics: Topic[];
  trending_topics: Topic[];
  recurring_questions: CommunityQuestion[];
  latest_summary: CommunitySummary | null;
  opportunities: Opportunity[];
  pending_memories: number;
  recent_activity: Activity[];
}

export interface CommunitySearch {
  query: string;
  window: { start: string | null; end: string | null; label: string | null };
  messages: { message: TelegramMessage; score: number; reason: string; source_name: string | null }[];
  memories: { memory: Memory; score: number }[];
  summaries: CommunitySummary[];
  topics: { topic: Topic; score: number }[];
  notes: string[];
  answer: { answer: string; cited: string[]; sufficient: boolean; model: string | null } | null;
}

export interface Provenance {
  kind: string;
  id: number;
  generated: Record<string, unknown> | null;
  messages: TelegramMessage[];
}

export interface Job {
  name: string;
  label: string;
  description: string;
  enabled: boolean;
  interval_seconds: number;
  status: string;
  running: boolean;
  last_run_at: string | null;
  last_finished_at: string | null;
  last_duration_ms: number | null;
  next_run_at: string | null;
  last_error: string | null;
  last_result: Record<string, unknown>;
  run_count: number;
  failure_count: number;
  consecutive_failures: number;
}

export interface JobRun {
  name: string;
  status: string;
  result: Record<string, unknown>;
  error: string | null;
}

export interface Route {
  task: string;
  label: string;
  description: string;
  default_tier: string;
  tier: string | null;
  model: string;
  temperature: number;
  customised: boolean;
}

export interface Routing {
  tiers: Record<string, string>;
  routes: Route[];
  installed_models: string[];
}

// --- Phase 3B/4A: secrets, Telegram setup, feedback, drafts, X, social actions ---------------------

/** Never contains a secret value, only whether one is set and where it comes from. */
export interface SecretStatus {
  name: string;
  configured: boolean;
  source: "dashboard" | "env" | null;
  saved_at: string | null;
}

export interface SetupStep {
  key: string;
  title: string;
  detail: string;
  done: boolean | null; // null = cannot be checked automatically
}

export interface TelegramSetup {
  token: SecretStatus;
  bot_username: string | null;
  privacy_disabled: boolean | null;
  ingestion_enabled: boolean;
  steps: SetupStep[];
}

export interface PilotJob {
  name: string;
  enabled: boolean;
  status: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  last_error: string | null;
  last_result: Record<string, unknown> | null;
}

export interface PilotStats {
  ingestion_enabled: boolean;
  telegram_status: string;
  telegram_error: string | null;
  authorised_chats: number;
  last_message_at: string | null;
  last_processed_at: string | null;
  messages_today: number;
  messages_total: number;
  messages_processed: number;
  messages_unprocessed: number;
  messages_ignored: number;
  community_memories: number;
  pending_memories: number;
  topics: number;
  questions: number;
  opportunities: number;
  jobs: PilotJob[];
}

export interface MessageTrace {
  message: TelegramMessage;
  source_name: string | null;
  topic: { id: number; name: string; status: string } | null;
  question: { id: number; text: string; ask_count?: number } | null;
  summaries: { id: number; headline?: string; kind?: string; model?: string }[];
  memories: { id: number; content?: string; review_status?: string }[];
  opportunities: { id: number; title?: string; status?: string }[];
}

export type FeedbackDecision = "approve" | "reject" | "edit" | "important" | "not_important";
export type FeedbackTarget = "memory" | "topic" | "question" | "opportunity" | "social_opportunity" | "draft";

export interface FeedbackEvent {
  id: number;
  target_kind: FeedbackTarget;
  target_id: number;
  decision: FeedbackDecision;
  features: string[];
  label: string;
  note: string;
  created_at: string;
}

export interface Preference {
  feature: string;
  approvals: number;
  rejections: number;
  weight: number;
}

export interface TopicBrief {
  id: number;
  name: string;
  description: string;
  message_count: number;
  aliases: string[];
  status: string;
}

export interface MergeSuggestion {
  id: number;
  topic_a: TopicBrief;
  topic_b: TopicBrief;
  similarity: number;
  shared_participants: number;
  shared_threads: number;
  verdict: string;
  reason: string;
  judged_by: string | null;
  status: string;
  created_at: string;
}

export type DraftType =
  | "x_post"
  | "x_thread"
  | "telegram_post"
  | "youtube_idea"
  | "youtube_titles"
  | "youtube_outline"
  | "short_video_idea"
  | "short_hook"
  | "tiktok_idea";

export interface Draft {
  id: number;
  title: string | null;
  body: string;
  status: string;
  draft_type: DraftType | null;
  opportunity_id: number | null;
  variant_group: string | null;
  variant_label: string | null;
  context: Record<string, unknown>;
  created_by: string;
  created_at: string;
}

export type CapabilityStatus = "available" | "unavailable" | "not_configured" | "disabled" | "error" | "unknown" | "no_credit" | "rate_limited" | "budget" | "approval";

export interface Capability {
  status: CapabilityStatus;
  detail?: string;
  label?: string;
  checked_at?: string;
}

export interface XUsage {
  requests: number;
  posts: number;
  users: number;
  cost: number;
}

export interface XStatus {
  configured: boolean;
  status: string;
  credentials: SecretStatus[];
  auth_modes: string[];
  account_username: string | null;
  account_id: string | null;
  account_name: string | null;
  capabilities: Record<string, Capability>;
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  paused: boolean;
  blocked_until: string | null;
  usage_today: XUsage;
  usage_month: XUsage;
  budget: { daily_usd: number; monthly_usd: number };
}

export interface XCredentialsInput {
  bearer_token?: string;
  api_key?: string;
  api_secret?: string;
  access_token?: string;
  access_token_secret?: string;
  username?: string;
}

export type MonitorRuleType = "keyword" | "search" | "topic" | "account";

export interface MonitorRule {
  id: number;
  platform: Platform;
  name: string;
  type: MonitorRuleType;
  query: string;
  enabled: boolean;
  priority: number;
  max_results: number;
  last_checked_at: string | null;
  last_result_count: number;
  posts_matched: number;
  last_error: string | null;
  last_success_at: string | null;
  posts_24h: number;
}

export type WatchCategory = "major_creator" | "industry" | "news" | "project" | "community_member" | "partner" | "emerging_creator" | "other";

export interface SocialProfile {
  id: number;
  platform: Platform;
  external_id: string;
  username: string;
  display_name: string | null;
  followers_count: number | null;
  following_count: number | null;
  post_count: number | null;
  verified: boolean | null;
  last_seen_at: string | null;
}

export interface Relationship {
  first_interaction_at: string | null;
  last_interaction_at: string | null;
  interaction_count: number;
  mention_count: number;
  reply_count: number;
  our_reply_count: number;
  topics: Record<string, number>;
  they_follow_us: boolean | null;
  we_follow_them: boolean | null;
  notes: string;
}

export interface WatchedAccount {
  id: number;
  profile: SocialProfile;
  reason: string;
  category: WatchCategory;
  priority: number;
  notes: string;
  enabled: boolean;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  relationship: Relationship | null;
}

export interface SocialPost {
  id: number;
  platform: Platform;
  external_id: string | null;
  url: string | null;
  text: string;
  author_username: string | null;
  author_name: string | null;
  author_followers: number | null;
  posted_at: string | null;
  is_own: boolean;
  source_kinds: string[];
  rule_ids: number[];
  metrics: Record<string, number | null>;
  lang: string | null;
  in_reply_to_external_id: string | null;
  ingested_at: string | null;
  topics: string[];
}

export type SocialOpportunityStatus = "new" | "reviewing" | "accepted" | "dismissed" | "expired";

export interface SocialOpportunity {
  id: number;
  platform: Platform;
  kind: string;
  topic: string;
  relevance: number;
  why: string;
  contribution: string;
  suggested_action: string;
  knowledge: { id?: number; title?: string | null; content?: string }[];
  engagement: Record<string, unknown>;
  signals: Record<string, number>;
  reasons: string[];
  priority: number;
  status: SocialOpportunityStatus;
  model: string | null;
  generated_at: string | null;
  post: SocialPost;
}

export interface CrossSignal {
  id: number;
  topic_name: string;
  telegram_topic_id: number | null;
  similarity: number;
  telegram_messages_24h: number;
  telegram_messages_total: number;
  telegram_participants: number;
  telegram_trend: string;
  x_posts: number;
  x_posts_24h: number;
  x_engagement_24h: number | null;
  x_engagement_prev_24h: number | null;
  x_trend: string;
  x_post_ids: number[];
  recommendation: string;
  status: string;
  first_detected_at: string;
  last_updated_at: string;
  opportunity_id: number | null;
  x_authors: number;
  x_watched_authors: number;
  x_author_names: string[];
  early_signal: boolean;
  lead_minutes: number | null;
}

export interface MetricSnapshot {
  captured_at: string;
  source: string;
  impressions: number | null;
  views: number | null;
  likes: number | null;
  replies: number | null;
  reposts: number | null;
  quotes: number | null;
  bookmarks: number | null;
  profile_visits: number | null;
  url_clicks: number | null;
  followers_attributed: number | null;
}

export interface XAnalytics {
  posts: SocialPost[];
  totals: Record<string, number | null>;
  post_count: number;
  note: string;
}

export interface XOverview {
  status: XStatus;
  monitored_accounts: number;
  rules: number;
  posts_total: number;
  posts_today: number;
  mentions: number;
  open_opportunities: number;
  topic_activity: { id: number; name: string; posts_24h: number; posts_prev_24h: number; trend: TopicTrend; enabled: boolean }[];
  cost_note: string;
}

export type PermissionMode = "disabled" | "approval" | "automatic";

export interface Permission {
  platform: Platform;
  action: SocialAction;
  mode: PermissionMode;
  max_per_hour: number;
  max_per_day: number;
  available_modes: PermissionMode[];
}

export interface GuardSettings {
  kill_switch: boolean;
  writes_available: boolean;
  social_actions_active: boolean;
  dry_run: boolean;
  automatic_locked_reason: string;
  platforms_enabled: Record<string, boolean>;
  blocked_targets: string[];
  watchlist_only: boolean;
  cooldown_minutes: number;
  duplicate_window_hours: number;
  permissions: Permission[];
  note: string;
}

export interface GuardCheck {
  check: string;
  passed: boolean;
  detail?: string;
}

export interface GuardDecision {
  allowed: boolean;
  requires_approval: boolean;
  executable: boolean;
  reason: string;
  checks: GuardCheck[];
}

export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired" | "executed" | "failed";

export interface ApprovalPayload {
  text?: string | null;
  original_text?: string;
  original_post?: { id: string | null; text: string; url: string | null; author: string | null; posted_at: string | null };
  target_profile?: { username: string; name: string | null; followers: number | null };
  quote_post_id?: string | null;
  topic?: string;
  reason?: string | null;
  model?: string | null;
  confidence?: string;
  context?: string[];
  warnings?: string[];
  variants?: { style: string; text: string; warnings: string[] }[];
  origin?: string;
  priority?: string;
  post_url?: string | null;
  target_author_id?: string | null;
  daily_likes_used?: number;
  daily_limit?: number;
  hourly_likes_used?: number;
  hourly_limit?: number;
  [k: string]: unknown;
}

export interface Approval {
  id: number;
  platform: Platform;
  action: SocialAction;
  target_external_id: string | null;
  target_username: string | null;
  payload: ApprovalPayload;
  source_kind: string;
  source_id: number | null;
  status: ApprovalStatus;
  guard_result: Partial<GuardDecision>;
  decided_at: string | null;
  expires_at: string | null;
  executed_at: string | null;
  execution_result: {
    executed?: boolean;
    dry_run?: boolean;
    blocked?: boolean;
    would_execute?: Record<string, string | null>;
    x_id?: string;
    url?: string;
    error?: string;
    error_code?: string;
    error_kind?: string;
    executed_at?: string;
    uncertain?: boolean;
    reason?: string;
    [k: string]: unknown;
  };
  notes: string;
  created_at: string;
  performance: Record<string, number | null> | null;
}

export interface XOperator {
  x_status: string;
  username: string | null;
  user_context: boolean;
  write_capability: Record<string, string>;
  social_actions_active: boolean;
  dry_run: boolean;
  mode: string;
  automatic: string;
  today: { action: SocialAction; done: number; limit: number; per_hour: number; enabled: boolean }[];
  pending_approvals: number;
  opportunities: number;
  ai: { screening_model: string | null; analysis_model: string | null; analysis_status: string | null; last_analysis_at: string | null };
  usage_today: XUsage & { writes?: number; write_cost?: number };
  learning: { key: string; text: string }[];
}

export interface GateStatus {
  interactive_active: boolean;
  background_task: string | null;
  background_seconds: number | null;
  background_waiting: number;
}

export interface Processing {
  gate: GateStatus;
  running_jobs: { name: string; label: string }[];
}

export type PilotCheckStatus = "ready" | "needs_setup" | "waiting" | "problem" | "disabled" | "paused" | "dry_run" | "supervised";

export interface ModelInfo {
  service: boolean;
  detail: string | null;
  fast: { model: string; installed: boolean };
  strong: { model: string | null; installed: boolean };
  embeddings: { model: string | null; installed: boolean };
}

export interface PilotStatus {
  ready: boolean;
  checks: { key: string; label: string; status: PilotCheckStatus; detail: string; next_step: string | null }[];
  models: ModelInfo;
}

export interface JobQueue {
  running: { name: string; label: string } | null;
  waiting: { name: string; label: string }[];
}

export interface CommandCenter {
  pilot: PilotStatus;
  ai: { online: boolean; model: string; provider: string; strong_model: string; fast_model: string; models: ModelInfo; processing: GateStatus; jobs: JobQueue };
  telegram: { status: string; communities: number; messages_today: number; pending_memories: number; topics: number };
  x: { status: string; username: string | null; monitored_accounts: number; rules: number; posts_today: number; posts_total: number; opportunities: number };
  trending: { id: number; name: string; trend: TopicTrend; messages_24h: number; rising: boolean }[];
  cross_signals: CrossSignal[];
  content: {
    opportunities: number;
    drafts_awaiting_review: number;
    approvals_pending: number;
    top: { id: number; title: string; priority: string; reasons: string[]; platforms: string[] }[];
  };
  attention: { failing_jobs: string[] };
  recent_activity: Activity[];
}


// --- Growth pilot: Telegram account mode, unified feed, review sessions --------------------------------

export interface TelegramAccountStatus {
  configured: boolean;
  status: "not_configured" | "needs_login" | "code_sent" | "password_needed" | "qr_waiting" | "connected" | "error";
  api_id: number | null;
  api_hash: SecretStatus;
  session: "active" | "none";
  user_id: number | null;
  username: string | null;
  display_name: string | null;
  phone_hint: string | null;
  authorized_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  requests_total: number;
  result?: "connected" | "password_needed";
}

export interface TelegramDialog {
  chat_id: number;
  title: string;
  chat_type: string;
  username: string | null;
  members: number | null;
  authorized: boolean;
}

export interface TelegramConnection {
  mode: ("bot" | "account")[];
  status: "connected" | "disconnected" | "needs_login" | "error" | "not_configured";
  bot: { configured: boolean; status: string; username: string | null; name: string | null; last_success_at: string | null; last_error: string | null };
  account: TelegramAccountStatus;
  sources: { total: number; enabled: number; bot: number; account: number };
  messages_today: number;
  last_message_at: string | null;
  last_processing_at: string | null;
}

export interface FeedItem {
  key: string;
  kind: "x" | "telegram";
  id: number;
  source: "X" | "Telegram" | "Telegram + X";
  topic: string;
  title: string;
  why: string;
  evidence: string[];
  recency_hours: number | null;
  relationship: string | null;
  community: string | null;
  knowledge: string[];
  suggested_actions: string[];
  signals: Record<string, number>;
  priority: number;
  label: "high" | "medium" | "low";
  reasons: string[];
  problems: string[];
  passed: boolean;
  post: { id: number; external_id: string | null; url: string | null; text: string; author: string | null; followers: number | null; posted_at: string | null } | null;
  pending_approval_id: number | null;
}

export interface GrowthRow {
  key: string;
  label: string;
  status: "ready" | "needs_setup" | "waiting" | "problem" | "paused" | "dry_run" | "locked" | "disabled" | "supervised";
  detail: string;
  next_step: string | null;
}

export interface GrowthToday {
  telegram_messages: number;
  x_posts_read: number;
  cross_signals: number;
  early_signals: number;
  opportunities_found: number;
  drafts_generated: number;
  approved: number;
  edited: number;
  rejected: number;
  skipped: number;
  executed: number;
  failed: number;
}

export interface GrowthStatus {
  rows: GrowthRow[];
  mode: string;
  social_actions_active: boolean;
  dry_run: boolean;
  automatic: string;
  today: GrowthToday;
}

export interface LearningSignal {
  key: string;
  text: string;
  count: number;
  of: number;
  state: "active" | "accepted" | "disabled";
}

export interface GrowthAnalytics {
  days: number;
  reviewed: number;
  approved_unchanged: number;
  approved_after_edit: number;
  rejected: number;
  skipped: number;
  ignored: number;
  approval_rate: number | null;
  edit_rate: number | null;
  rejection_rate: number | null;
  executed: number;
  by_origin: Record<string, { reviewed: number; accepted: number; executed: number }>;
  best_origin: string | null;
  top_topics: [string, number][];
  ai_drafts: { reviewed: number; approved_unchanged: number; approved_after_edit: number; rejected: number };
  relationships: { accounts_replied_to: number; of_which_interacted_since: number };
  note: string;
}

export interface GrowthUsage {
  days: number;
  x: { today: XUsage & { writes?: number; write_cost?: number }; month: XUsage; budget: { daily_usd: number; monthly_usd: number }; note: string };
  telegram: { requests: Record<string, number>; note: string };
  models: { fast_calls: number; strong_calls: number; fast_seconds: number; strong_seconds: number; by_task: Record<string, number> };
}

export interface Creator {
  profile_id: number;
  username: string;
  name: string | null;
  followers: number | null;
  why: string;
  topics: string[];
  appearances: number;
  community_overlap: string[];
  relationship: string;
  score: number;
}

// --- X likes pilot -----------------------------------------------------------------------------------

export interface LikeStatus {
  capability: "available" | "unavailable" | "unknown" | "needs_user_auth" | "not_configured";
  capability_detail: string;
  user_auth: boolean;
  account: string | null;
  endpoint: string;
  permission: "approval_required" | "disabled";
  automatic: "locked";
  dry_run: boolean;
  social_actions_active: boolean;
  used_today: number;
  daily_limit: number;
  used_this_hour: number;
  hourly_limit: number;
  account_cooldown_hours: number;
  cooldown_minutes: number;
  ready_to_execute: boolean;
  problems: string[];
}

export interface LikeCandidate {
  opportunity_id: number;
  suggested_action: "like";
  topic: string;
  reason: string;
  score: number;
  signals: string[];
  post: { id: number; external_id: string; url: string | null; text: string; author: string | null; author_id: string | null; followers: number | null; posted_at: string | null };
  relationship: { likes_this_week: number; watched: boolean };
  pending_approval_id: number | null;
  problems?: string[];
}

export interface LikePilot {
  status: LikeStatus;
  candidates: LikeCandidate[];
  more: number;
  filtered_out: number;
  filtered?: LikeCandidate[];
}

// --- Analytics › Growth ----------------------------------------------------------------------------------

export type GrowthPeriod = "24h" | "7d" | "30d" | "all";

export interface GrowthKpi {
  key: string;
  label: string;
  value: number | null;
  previous: number | null;
  change: { delta: number | null; pct: number | null };
  unit: "" | "%" | "$";
  available: boolean;
  note: string;
}

export interface GrowthChart {
  buckets: string[];
  step: "hour" | "day";
  followers: (number | null)[];
  follower_points: { t: string; v: number }[];
  impressions: (number | null)[];
  engagements: (number | null)[];
  posts: number[];
  replies: number[];
  likes: number[];
  reposts: number[];
  follows: number[];
  note: string;
}

export interface GrowthTimelineItem {
  at: string;
  action: string;
  title: string;
  account: string | null;
  topic: string | null;
  text: string | null;
  url: string | null;
  approval_id: number | null;
}

export interface GrowthContentRow {
  id: number;
  text: string;
  url: string | null;
  posted_at: string | null;
  kind: "post" | "reply";
  topic: string | null;
  via_price_chat: boolean;
  ai_drafted: boolean | null;
  human_edited: boolean | null;
  impressions: number | null;
  likes: number | null;
  replies: number | null;
  reposts: number | null;
  bookmarks: number | null;
  engagements: number | null;
  engagement_rate: number | null;
  checkpoints: Record<string, { impressions: number | null; likes: number | null; replies: number | null; reposts: number | null; bookmarks: number | null } | null>;
}

export interface GrowthDashboard {
  period: GrowthPeriod;
  generated_at: string;
  account: string | null;
  kpis: GrowthKpi[];
  chart: GrowthChart;
  timeline: GrowthTimelineItem[];
  content: GrowthContentRow[];
  ai: {
    opportunities_x: number;
    opportunities_telegram: number;
    opportunities_dismissed: number;
    proposals: number;
    approved: number;
    rejected: number;
    pending: number;
    expired: number;
    ai_drafts_reviewed: number;
    ai_acceptance_rate: number | null;
    ai_unchanged_rate: number | null;
    human_edit_rate: number | null;
    likes: { recommended: number; proposed: number; approved: number };
    replies: { recommended: number; proposed: number; approved: number };
    successful_actions: number;
    failed_or_blocked: number;
    trust_trend: { week_start: string; reviewed: number; approved_unchanged_rate: number | null }[];
  };
  relationships: {
    account: string;
    name: string | null;
    followers: number | null;
    likes_from_us: number;
    replies_from_us: number;
    reposts_from_us: number;
    we_follow: boolean | null;
    their_mentions: number;
    their_replies: number;
    last_interaction_at: string | null;
    first_interaction_at: string | null;
    topics: string[];
    history: { at: string; action: string }[];
  }[];
  telegram: {
    connected_sources: number;
    messages: number;
    messages_prev: number | null;
    active_discussions: number;
    topics_detected: number;
    topics_prev: number | null;
    questions_detected: number;
    questions_prev: number | null;
    content_opportunities: number;
    member_history: { chat: string; points: { t: string; v: number }[]; latest_members?: number | null }[];
    member_note: string;
  };
  cost: {
    today: { cost: number; requests: number; writes: number };
    d7: { cost: number; requests: number; writes: number };
    d30: { cost: number; requests: number; writes: number };
    per_useful_opportunity_30d: number | null;
    useful_opportunities_30d: number;
    per_approved_action_30d: number | null;
    approved_actions_30d: number;
    per_executed_action_30d: number | null;
    executed_actions_30d: number;
    budget: { daily_usd: number; monthly_usd: number };
    month: { cost: number; requests: number };
    note: string;
  };
  snapshots: { x_follower_snapshots: number; first_snapshot_at: string | null; schedule: string };
  not_provided: string[];
  disclaimer: string;
}

// --- YouTube + website content -----------------------------------------------------------------------------

export interface ContentItemRow {
  id: number;
  platform: string;
  external_id: string;
  channel_id: string | null;
  content_type: "short" | "long_form" | string;
  type_source: "auto" | "manual";
  type_reason: string;
  title: string;
  description: string;
  url: string;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  published_at: string | null;
  privacy_status: string;
  available: boolean;
  website_visible: boolean;
  visibility_source: "auto" | "manual";
  featured: boolean;
  on_website: boolean;
  metrics: { views?: number | null; likes?: number | null; comments?: number | null };
  last_synced_at: string | null;
  distribution: Record<string, string>;
  series: "whats_it_worth" | "originals" | null;
  category: "markets" | "crypto" | "money" | "culture" | null;
}

export interface YouTubeChannel {
  id: string;
  title: string | null;
  handle: string | null;
  subscribers: number | null;
  subscribers_hidden: boolean;
  videos: number | null;
  views: number | null;
  thumbnail: string | null;
  checked_at: string;
}

export interface YouTubeStatus {
  status: string;
  mode: "api_key" | "oauth" | null;
  api_key: { configured: boolean; source: string | null; saved_at: string | null };
  oauth_client: boolean;
  oauth_signed_in: boolean;
  redirect_uri: string;
  channel: YouTubeChannel | null;
  channels_available: YouTubeChannel[];
  last_sync: { last_sync_at: string; listed: number; new: number; updated: number; unavailable: number; requests: number } | null;
  next_sync_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  quota_today: { requests: number; units: number; daily_limit: number };
  content: { shorts: number; long_form: number; on_website: number; unavailable: number; latest: ContentItemRow | null };
  performance: { views: number | null; likes: number | null; comments: number | null };
  recent?: ContentItemRow[];
  result?: Record<string, unknown>;
}

export interface WebsiteCardOut {
  platform: string;
  external_id: string;
  type: string;
  type_label: string;
  title: string;
  url: string;
  thumbnail: string | null;
  duration: string | null;
  published_at: string | null;
  featured: boolean;
}

export interface WebsiteStatus {
  source: string;
  mode: string | null;
  status: string;
  channel: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  visible_items: number;
  showing: WebsiteCardOut[];
}

export interface ContentItemUpdate {
  website_visible?: boolean;
  featured?: boolean;
  content_type?: "short" | "long_form" | "auto";
  visibility?: "auto";
  series?: "whats_it_worth" | "originals" | "none";
  category?: "markets" | "crypto" | "money" | "culture" | "none";
}

export interface WebsiteStatusDetail extends WebsiteStatus {
  featured: WebsiteCardOut | null;
  home: import("../website/websiteLogic").HomeData;
  saved_links: Record<"telegram" | "x" | "youtube", string>;
  price_board_source: string | null;
}
