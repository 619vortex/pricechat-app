import type {
  Activity,
  ActivityCategory,
  ActivityLevel,
  ActionLimit,
  AgentBrain,
  AgentConfig,
  AgentTask,
  Brand,
  BrandProfile,
  ChatReply,
  CompiledPrompt,
  Connector,
  Content,
  ContentStatus,
  ConversationMessage,
  Goal,
  KnowledgeSaved,
  KnowledgeSearch,
  KnowledgeSource,
  Memory,
  MemoryKind,
  MemoryLayer,
  MemoryStats,
  ModelStatus,
  ModelTestResult,
  Overview,
  Platform,
  PromptPlatform,
  SafetyPolicy,
  SafetySettings,
  CommunityOverview,
  CommunityQuestion,
  CommunitySearch,
  CommunitySummary,
  ContentChannel,
  DeletionReport,
  Job,
  JobMonitor,
  JobRun,
  Opportunity,
  OpportunityStatus,
  Provenance,
  QuestionStatus,
  ReviewStatus,
  Routing,
  SummaryKind,
  TelegramMessage,
  TelegramSource,
  TelegramStatus,
  Topic,
  TopicDetail,
  Approval,
  ApprovalStatus,
  CommandCenter,
  CrossSignal,
  Draft,
  DraftType,
  FeedbackDecision,
  FeedbackEvent,
  FeedbackTarget,
  GuardDecision,
  GuardSettings,
  MergeSuggestion,
  MessageTrace,
  MetricSnapshot,
  MonitorRule,
  MonitorRuleType,
  PermissionMode,
  PilotStats,
  Preference,
  Processing,
  PilotStatus,
  XOperator,
  TelegramAccountStatus,
  TelegramDialog,
  TelegramConnection,
  FeedItem,
  GrowthStatus,
  LearningSignal,
  GrowthAnalytics,
  GrowthUsage,
  Creator,
  LikePilot,
  GrowthDashboard,
  YouTubeStatus,
  ContentItemRow,
  ContentItemUpdate,
  WebsiteStatusDetail,
  GrowthPeriod,
  Relationship,
  SocialAction,
  SocialOpportunity,
  SocialOpportunityStatus,
  SocialPost,
  TelegramSetup,
  WatchCategory,
  WatchedAccount,
  XAnalytics,
  XCredentialsInput,
  XOverview,
  XStatus,
} from "./types";

const BASE = "/api";
/** Fired when an admin API answers "admin login required": AdminGate shows the login screen. */
export const ADMIN_SIGNED_OUT = "pc-admin-signed-out";
const DEFAULT_TIMEOUT_MS = 30_000;
// Model calls can include loading a model into GPU memory on first use.
export const AI_TIMEOUT_MS = 180_000;

/** What went wrong, in terms the UI can explain to a non-developer. */
export type ErrorKind =
  | "backend_unavailable" // the Price Chat backend isn't running / reachable
  | "ai_unavailable" // backend is fine, the model service (e.g. Ollama) isn't
  | "model_unavailable" // model service is up, but the selected model isn't installed
  | "timeout" // the model took too long (AI requests only)
  | "backend_slow" // an ordinary request got no answer in time (backend busy or stuck), not AI
  | "paused" // agent is paused
  | "validation" // bad input
  | "not_found"
  | "unauthorized" // the owner login is required (or the session ended)
  | "error";

export class ApiError extends Error {
  constructor(
    public kind: ErrorKind,
    message: string,
    public status = 0,
  ) {
    super(message);
  }
}

const CODE_TO_KIND: Record<string, ErrorKind> = {
  provider_unavailable: "ai_unavailable",
  provider_timeout: "timeout",
  model_unavailable: "model_unavailable",
  agent_paused: "paused",
  provider_error: "ai_unavailable",
};

/** The public website's market reads are marked (?pub=1) so the server lets the CDN keep them; the dashboard's never are. */
function edgeMark(path: string, init: RequestInit): string {
  const pub = (init.method ?? "GET").toUpperCase() === "GET" && path.startsWith("/pokemon/") && !location.pathname.startsWith("/admin");
  return pub ? `${path.includes("?") ? "&" : "?"}pub=1` : "";
}

export async function request<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}${edgeMark(path, init)}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...init.headers },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      // Only requests that wait for a model use the long AI timeout; anything else timing out is the backend.
      if (timeoutMs >= AI_TIMEOUT_MS) throw new ApiError("timeout", `No answer after ${Math.round(timeoutMs / 1000)} seconds.`);
      throw new ApiError("backend_slow", `The Price Chat backend did not answer within ${Math.round(timeoutMs / 1000)} seconds. It may be busy; try again in a moment.`);
    }
    throw new ApiError("backend_unavailable", "Cannot reach the Price Chat backend. Is it running on port 8000?");
  } finally {
    window.clearTimeout(timer);
  }

  if (res.ok) {
    return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
  }

  let body: { detail?: unknown; code?: string } | null = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  // No JSON body on a 5xx means the dev proxy answered: the backend itself is down.
  if (body === null && res.status >= 500) {
    throw new ApiError("backend_unavailable", "The Price Chat backend is not responding. Is it running on port 8000?", res.status);
  }
  if (res.status === 401 && body?.code === "admin_auth") {
    window.dispatchEvent(new Event(ADMIN_SIGNED_OUT)); // the dashboard shows its login screen
    throw new ApiError("unauthorized", "Please sign in again.", 401);
  }
  let message = `${res.status} ${res.statusText}`;
  if (typeof body?.detail === "string") message = body.detail;
  else if (Array.isArray(body?.detail)) message = body.detail.map((d: { loc?: string[]; msg: string }) => `${d.loc?.slice(-1)[0] ?? ""}: ${d.msg}`).join("; ");

  const kind: ErrorKind =
    (body?.code && CODE_TO_KIND[body.code]) || (res.status === 422 ? "validation" : res.status === 404 ? "not_found" : "error");
  throw new ApiError(kind, message, res.status);
}

export type OwnerAlerts = {
  enabled: boolean;
  telegram_chat: string;
  /** false: an alert would only reach the server logs right now; `problem` says why ("Owner alerts go nowhere: ...") */
  delivers?: boolean;
  problem?: string | null;
  recent: { key: string; last_sent_at: string | null; sent_count: number; open: boolean; text: string }[];
};

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
const qs = (params: Record<string, string | number | undefined | null>) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}` : "";
};

export const api = {
  overview: () => request<Overview>("/overview"),

  // model
  model: () => request<ModelStatus>("/model"),
  updateModel: (changes: { model_name?: string; temperature?: number | null }) =>
    request<ModelStatus>("/model", json("PATCH", changes)),
  testModel: () => request<ModelTestResult>("/model/test", { method: "POST" }, AI_TIMEOUT_MS),

  // agent
  agent: () => request<AgentBrain>("/agent"),
  updateAgent: (changes: Partial<Pick<AgentConfig, "name" | "identity" | "behavior_rules" | "status">>) =>
    request<AgentConfig>("/agent/config", json("PATCH", changes)),
  chat: (message: string, conversationId: string, task: AgentTask, platform: PromptPlatform) =>
    request<ChatReply>(
      "/agent/chat",
      json("POST", { message, conversation_id: conversationId, task, platform }),
      AI_TIMEOUT_MS,
    ),
  conversation: (conversationId: string) =>
    request<ConversationMessage[]>(`/agent/conversations/${encodeURIComponent(conversationId)}`),
  prompt: (task: AgentTask, platform: PromptPlatform, query?: string) =>
    request<CompiledPrompt>(`/agent/prompt${qs({ task, platform, query })}`, {}, 60_000),

  // brand + goals
  brand: () => request<Brand>("/brand"),
  updateBrand: (changes: Partial<Omit<BrandProfile, "updated_at">>) => request<Brand>("/brand", json("PATCH", changes)),
  goals: () => request<Goal[]>("/goals"),
  createGoal: (goal: { name: string; description: string; priority: number; enabled: boolean }) =>
    request<Goal>("/goals", json("POST", goal)),
  updateGoal: (id: number, changes: Partial<Omit<Goal, "id" | "updated_at">>) =>
    request<Goal>(`/goals/${id}`, json("PATCH", changes)),
  deleteGoal: (id: number) => request<void>(`/goals/${id}`, { method: "DELETE" }),

  // knowledge
  knowledge: (filters: { layer?: MemoryLayer | ""; kind?: MemoryKind | ""; tag?: string; review_status?: ReviewStatus | ""; source_type?: string } = {}) =>
    request<Memory[]>(`/knowledge${qs({ ...filters, limit: 200 })}`),
  searchKnowledge: (q: string, layer?: MemoryLayer | "") =>
    request<KnowledgeSearch>(`/knowledge/search${qs({ q, layer, limit: 20 })}`),
  knowledgeStats: () => request<MemoryStats>("/knowledge/stats"),
  knowledgeSources: () => request<KnowledgeSource[]>("/knowledge/sources"),
  addKnowledge: (item: { title?: string; content: string; kind: MemoryKind; tags: string[]; source?: string; importance: number }) =>
    request<KnowledgeSaved>("/knowledge", json("POST", item)),
  updateKnowledge: (id: number, changes: Partial<{ title: string; content: string; kind: MemoryKind; tags: string[]; source: string; importance: number; review_status: ReviewStatus }>) =>
    request<KnowledgeSaved>(`/knowledge/${id}`, json("PATCH", changes)),
  deleteKnowledge: (id: number) => request<void>(`/knowledge/${id}`, { method: "DELETE" }),
  reindexKnowledge: () => request<{ indexed: number; embedding_model: string | null }>("/knowledge/reindex", { method: "POST" }, AI_TIMEOUT_MS),

  // content
  content: () => request<Content[]>("/content"),
  createContent: (body: { body: string; title?: string; status: ContentStatus; target_platform: Platform | null }) =>
    request<Content>("/content", json("POST", body)),
  updateContent: (id: number, changes: Partial<Content>) => request<Content>(`/content/${id}`, json("PATCH", changes)),
  deleteContent: (id: number) => request<void>(`/content/${id}`, { method: "DELETE" }),

  connectors: () => request<Connector[]>("/connectors"),

  // telegram
  telegramStatus: () => request<TelegramStatus>("/telegram/status"),
  telegramVerify: () => request<TelegramStatus>("/telegram/verify", { method: "POST" }),
  telegramSync: () => request<JobRun>("/telegram/sync", { method: "POST" }, 90_000),
  addTelegramSource: (chat_id: number, display_name?: string) =>
    request<TelegramSource>("/telegram/sources", json("POST", { chat_id, display_name: display_name || undefined })),
  updateTelegramSource: (id: number, changes: Partial<Pick<TelegramSource, "display_name" | "enabled" | "ingestion_enabled">>) =>
    request<TelegramSource>(`/telegram/sources/${id}`, json("PATCH", changes)),
  deleteTelegramSource: (id: number) => request<DeletionReport>(`/telegram/sources/${id}`, { method: "DELETE" }),
  telegramMessages: (filters: { source_id?: number; topic_id?: number; question_id?: number; q?: string; questions_only?: boolean; offset?: number } = {}) =>
    request<TelegramMessage[]>(`/telegram/messages${qs({ ...filters, questions_only: filters.questions_only ? "true" : undefined, limit: 100 })}`),
  deleteTelegramMessage: (id: number) => request<DeletionReport>(`/telegram/messages/${id}`, { method: "DELETE" }),

  // community intelligence
  communityOverview: () => request<CommunityOverview>("/community/overview"),
  topics: (sort: "importance" | "volume" | "recent" | "trending" = "importance", status = "active") =>
    request<Topic[]>(`/community/topics${qs({ sort, status })}`),
  topic: (id: number) => request<TopicDetail>(`/community/topics/${id}`),
  questions: (recurringOnly = true, status?: QuestionStatus) =>
    request<CommunityQuestion[]>(`/community/questions${qs({ recurring_only: recurringOnly ? "true" : "false", status })}`),
  updateQuestion: (id: number, changes: { status?: QuestionStatus; text?: string }) =>
    request<CommunityQuestion>(`/community/questions/${id}`, json("PATCH", changes)),
  questionMessages: (id: number) => request<TelegramMessage[]>(`/community/questions/${id}/messages`),
  questionFaq: (id: number, answer: string) => request<CommunityQuestion>(`/community/questions/${id}/faq`, json("POST", { answer })),
  summaries: (kind?: SummaryKind) => request<CommunitySummary[]>(`/community/summaries${qs({ kind })}`),
  summary: (id: number) => request<{ summary: CommunitySummary; messages: TelegramMessage[] }>(`/community/summaries/${id}`),
  createSummary: (body: { kind: SummaryKind; source_id?: number; topic_id?: number; hours?: number }) =>
    request<CommunitySummary>("/community/summaries", json("POST", body), AI_TIMEOUT_MS * 2),
  communitySearch: (q: string, answer: boolean) =>
    request<CommunitySearch>(`/community/search${qs({ q, answer: answer ? "true" : undefined })}`, {}, AI_TIMEOUT_MS),
  provenance: (kind: string, id: number) => request<Provenance>(`/community/provenance/${kind}/${id}`),

  // content opportunities
  opportunities: (status?: OpportunityStatus) => request<Opportunity[]>(`/opportunities${qs({ status })}`),
  opportunity: (id: number) => request<{ opportunity: Opportunity; messages: TelegramMessage[] }>(`/opportunities/${id}`),
  updateOpportunity: (id: number, changes: Partial<Pick<Opportunity, "status" | "notes" | "title" | "angle" | "platforms">>) =>
    request<Opportunity>(`/opportunities/${id}`, json("PATCH", changes)),
  draftOpportunity: (id: number, channel: ContentChannel) =>
    request<Content>(`/opportunities/${id}/draft`, json("POST", { channel }), AI_TIMEOUT_MS * 2),

  // background jobs & model routing
  jobs: () => request<Job[]>("/jobs"),
  jobMonitors: () => request<JobMonitor[]>("/jobs/monitors"),
  runJob: (name: string) => request<JobRun>(`/jobs/${encodeURIComponent(name)}/run`, { method: "POST" }, AI_TIMEOUT_MS * 3),
  setJobEnabled: (name: string, enabled: boolean) => request<JobRun>(`/jobs/${encodeURIComponent(name)}`, json("PATCH", { enabled })),
  routing: () => request<Routing>("/model/routes"),
  setRoute: (task: string, body: { tier?: string; model?: string; temperature?: number }) =>
    request<Routing>(`/model/routes/${encodeURIComponent(task)}`, json("PUT", body)),

  activity: (filters: { level?: ActivityLevel; category?: ActivityCategory } = {}) =>
    request<Activity[]>(`/activity${qs({ ...filters, limit: 200 })}`),

  // safety
  safety: () => request<SafetySettings>("/settings/safety"),
  updateSafety: (changes: Partial<Pick<SafetyPolicy, "mode" | "kill_switch" | "max_actions_per_hour" | "max_actions_per_day">>) =>
    request<SafetySettings>("/settings/safety", json("PATCH", changes)),
  updateLimit: (id: number, changes: Partial<Omit<ActionLimit, "id" | "platform" | "action">>) =>
    request<ActionLimit>(`/settings/safety/limits/${id}`, json("PATCH", changes)),
  // telegram setup (the token is sent once and never returned)
  telegramSetup: () => request<TelegramSetup>("/telegram/setup"),
  saveTelegramToken: (token: string) => request<TelegramSetup>("/telegram/credentials", json("PUT", { token, verify: true }), 30_000),
  removeTelegramToken: () => request<TelegramSetup>("/telegram/credentials", { method: "DELETE" }),
  refreshTelegramChats: () => request<TelegramStatus>("/telegram/refresh-chats", { method: "POST" }, 60_000),
  setTelegramIngestion: (enabled: boolean) => request<TelegramSetup>("/telegram/ingestion", json("PUT", { enabled })),
  messageTrace: (id: number) => request<MessageTrace>(`/telegram/messages/${id}/trace`),
  pilot: () => request<PilotStats>("/community/pilot"),

  // human feedback & learned preferences
  feedback: (kind: FeedbackTarget, id: number, decision: FeedbackDecision, extra: { note?: string; edits?: Record<string, unknown> } = {}) =>
    request<FeedbackEvent>(`/feedback/${kind}/${id}`, json("POST", { decision, ...extra })),
  feedbackHistory: (kind?: FeedbackTarget) => request<FeedbackEvent[]>(`/feedback${qs({ kind })}`),
  preferences: (scope: "opportunity" | "social_opportunity" = "opportunity") => request<Preference[]>(`/feedback/preferences${qs({ scope })}`),

  // topic merge / split (suggestions only; a human decides)
  mergeSuggestions: (status = "pending") => request<MergeSuggestion[]>(`/community/merges${qs({ status })}`),
  mergeTopics: (id: number, keep: "a" | "b" | "larger" = "larger") => request<Topic>(`/community/merges/${id}/merge${qs({ keep })}`, { method: "POST" }),
  keepTopicsSeparate: (id: number) => request<void>(`/community/merges/${id}/keep-separate`, { method: "POST" }),
  splitTopic: (topicId: number, message_ids: number[], name: string) =>
    request<Topic>(`/community/topics/${topicId}/split`, json("POST", { message_ids, name })),

  // drafts & ranking
  generateDrafts: (oppId: number, draft_type: DraftType, variants = 1) =>
    request<Draft[]>(`/opportunities/${oppId}/drafts`, json("POST", { draft_type, variants }), AI_TIMEOUT_MS * 3),
  drafts: (opportunityId?: number) => request<Draft[]>(`/drafts${qs({ opportunity_id: opportunityId })}`),
  rankOpportunities: () => request<{ ranked: number }>("/opportunities/rank", { method: "POST" }, AI_TIMEOUT_MS),

  // X (read-only)
  xStatus: () => request<XStatus>("/x/status"),
  xOverview: () => request<XOverview>("/x/overview"),
  saveXCredentials: (body: XCredentialsInput) => request<XStatus>("/x/credentials", json("PUT", body)),
  removeXCredentials: () => request<XStatus>("/x/credentials", { method: "DELETE" }),
  testX: () => request<XStatus>("/x/test", { method: "POST" }, 60_000),
  setXReading: (enabled: boolean) => request<XStatus>("/x/reading", json("PUT", { enabled })),
  syncX: () => request<JobRun>("/x/sync", { method: "POST" }, AI_TIMEOUT_MS),
  xRules: () => request<MonitorRule[]>("/x/rules"),
  addXRule: (body: { name: string; type: MonitorRuleType; query: string; max_results?: number; priority?: number }) =>
    request<MonitorRule>("/x/rules", json("POST", body)),
  updateXRule: (id: number, changes: Partial<Pick<MonitorRule, "name" | "query" | "enabled" | "priority" | "max_results">>) =>
    request<MonitorRule>(`/x/rules/${id}`, json("PATCH", changes)),
  deleteXRule: (id: number) => request<void>(`/x/rules/${id}`, { method: "DELETE" }),
  xWatchlist: () => request<WatchedAccount[]>("/x/watchlist"),
  watchX: (body: { username: string; category: WatchCategory; reason?: string; priority?: number }) =>
    request<WatchedAccount>("/x/watchlist", json("POST", body), 30_000),
  updateWatch: (id: number, changes: Partial<Pick<WatchedAccount, "reason" | "category" | "priority" | "notes" | "enabled">>) =>
    request<WatchedAccount>(`/x/watchlist/${id}`, json("PATCH", changes)),
  unwatchX: (id: number) => request<void>(`/x/watchlist/${id}`, { method: "DELETE" }),
  setRelationshipNotes: (profileId: number, notes: string) => request<Relationship>(`/x/profiles/${profileId}/relationship`, json("PUT", { notes })),
  xPosts: (filters: { source?: "own" | "mention" | "search" | "watchlist"; rule_id?: number; profile_id?: number } = {}) =>
    request<SocialPost[]>(`/x/posts${qs(filters)}`),
  xPostMetrics: (id: number) => request<MetricSnapshot[]>(`/x/posts/${id}/metrics`),
  xAnalytics: () => request<XAnalytics>("/x/analytics"),
  xOpportunities: (status: SocialOpportunityStatus = "new") => request<SocialOpportunity[]>(`/x/opportunities${qs({ status })}`),
  updateXOpportunity: (id: number, status: SocialOpportunityStatus) => request<SocialOpportunity>(`/x/opportunities/${id}`, json("PATCH", { status })),
  proposeXAction: (id: number, body: { action?: SocialAction; text?: string }) => request<Approval>(`/x/opportunities/${id}/propose`, json("POST", body)),
  draftXReply: (id: number, body: { standalone?: boolean; direction?: string } = {}) =>
    request<Approval>(`/x/opportunities/${id}/draft-reply`, json("POST", body), AI_TIMEOUT_MS * 2),
  prepareTestPost: () => request<Approval>("/x/test-post", { method: "POST" }),
  xOperator: () => request<XOperator>("/x/operator"),
  setSocialActions: (active: boolean) => request<GuardSettings>("/social/actions", json("PUT", { active })),

  // cross-platform signals
  crossSignals: (status = "active") => request<CrossSignal[]>(`/cross-signals${qs({ status })}`),
  dismissCrossSignal: (id: number) => request<CrossSignal>(`/cross-signals/${id}/dismiss`, { method: "POST" }),

  // social action guard & approval queue (no write is executed in this phase)
  guard: () => request<GuardSettings>("/social/guard"),
  updateGuard: (changes: Partial<Pick<GuardSettings, "dry_run" | "platforms_enabled" | "blocked_targets" | "watchlist_only" | "cooldown_minutes" | "duplicate_window_hours">>) =>
    request<GuardSettings>("/social/guard", json("PATCH", changes)),
  setPermission: (platform: Platform, action: SocialAction, changes: { mode?: PermissionMode; max_per_hour?: number; max_per_day?: number }) =>
    request<GuardSettings>(`/social/permissions/${platform}/${action}`, json("PUT", changes)),
  guardCheck: (body: { platform: Platform; action: SocialAction; target?: string; text?: string }) =>
    request<GuardDecision>("/social/guard/check", json("POST", body)),
  approvals: (status?: ApprovalStatus) => request<Approval[]>(`/approvals${qs({ status })}`),
  proposeApproval: (body: { platform: Platform; action: SocialAction; text?: string; target_username?: string; target_external_id?: string }) =>
    request<Approval>("/approvals", json("POST", body)),
  approvalFromDraft: (contentId: number) => request<Approval>(`/approvals/from-draft/${contentId}`, { method: "POST" }),
  // Approving can send the action to X, so allow time for the request.
  decideApproval: (id: number, decision: "approved" | "rejected", note = "", text?: string) =>
    request<Approval>(`/approvals/${id}/decision`, json("POST", { decision, note, ...(text !== undefined ? { text } : {}) }), 60_000),

  processing: () => request<Processing>("/system/processing"),
  // owner alerts: a private Telegram message to the owner when a job keeps failing, the backup fails or TikTok signs out
  ownerAlerts: () => request<OwnerAlerts>("/system/alerts"),
  updateOwnerAlerts: (body: { enabled?: boolean; telegram_chat?: string }) => request<OwnerAlerts>("/system/alerts", json("PUT", body)),
  testOwnerAlert: () => request<{ sent: boolean }>("/system/alerts/test", { method: "POST" }),
  linkOwnerAlerts: () => request<{ url: string; expires_at: string }>("/system/alerts/link", { method: "POST" }),

  // Telegram account mode (official client API). Codes and passwords go straight to Telegram; nothing is stored.
  telegramConnection: () => request<TelegramConnection>("/telegram/connection"),
  tgAccount: () => request<TelegramAccountStatus>("/telegram/account"),
  tgAccountCredentials: (api_id: number, api_hash?: string) =>
    request<TelegramAccountStatus>("/telegram/account/credentials", json("PUT", { api_id, ...(api_hash ? { api_hash } : {}) })),
  tgAccountForget: () => request<TelegramAccountStatus>("/telegram/account/credentials", { method: "DELETE" }),
  tgLogin: (phone: string) => request<TelegramAccountStatus>("/telegram/account/login", json("POST", { phone }), 60_000),
  tgCode: (code: string) => request<TelegramAccountStatus>("/telegram/account/code", json("POST", { code }), 60_000),
  tgPassword: (password: string) => request<TelegramAccountStatus>("/telegram/account/password", json("POST", { password }), 60_000),
  tgCancel: () => request<TelegramAccountStatus>("/telegram/account/cancel", { method: "POST" }),
  tgQrStart: () => request<{ url: string; account: TelegramAccountStatus }>("/telegram/account/qr", { method: "POST" }, 60_000),
  tgQrPoll: () => request<{ status: "waiting" | "connected" | "password_needed"; url?: string; account: TelegramAccountStatus }>("/telegram/account/qr/poll", { method: "POST" }, 60_000),
  tgLogout: () => request<TelegramAccountStatus>("/telegram/account/logout", { method: "POST" }, 60_000),
  tgDialogs: () => request<TelegramDialog[]>("/telegram/account/dialogs", { method: "POST" }, 60_000),
  tgAuthorize: (chat_id: number) => request<TelegramSource>("/telegram/account/sources", json("POST", { chat_id })),
  tgImportEstimate: (sourceId: number, hours: number) =>
    request<{ hours: number; estimated_messages: number | null; cap: number; note: string }>(`/telegram/account/sources/${sourceId}/import-estimate${qs({ hours })}`, {}, 60_000),
  tgImport: (sourceId: number, hours: number) =>
    request<{ stored: number; duplicates: number; capped: boolean; cap: number }>(`/telegram/account/sources/${sourceId}/import`, json("POST", { hours }), 600_000),
  questionToOpportunity: (id: number) => request<{ id: number; created: boolean }>(`/community/questions/${id}/opportunity`, { method: "POST" }),

  // Growth pilot
  growthStatus: () => request<GrowthStatus>("/growth/status", {}, 30_000),
  growthFeed: (includeFiltered = true, action?: string) =>
    request<FeedItem[]>(`/growth/feed${qs({ include_filtered: includeFiltered ? "true" : "false", ...(action ? { action } : {}) })}`),
  // YouTube (read-only) and website content
  youtube: () => request<YouTubeStatus>("/youtube"),
  youtubeApiKey: (channel: string, api_key?: string) => request<YouTubeStatus>("/youtube/api-key", json("PUT", { channel, ...(api_key ? { api_key } : {}) }), 60_000),
  youtubeOAuthClient: (client_id: string, client_secret?: string) =>
    request<YouTubeStatus>("/youtube/oauth-client", json("PUT", { client_id, ...(client_secret ? { client_secret } : {}) })),
  youtubeOAuthStart: () => request<{ url: string }>("/youtube/oauth/start", { method: "POST" }),
  youtubeChannel: (channel_id: string) => request<YouTubeStatus>("/youtube/channel", json("POST", { channel_id }), 60_000),
  youtubeTest: () => request<YouTubeStatus>("/youtube/test", { method: "POST" }, 60_000),
  youtubeSync: () => request<YouTubeStatus>("/youtube/sync", { method: "POST" }, 180_000),
  youtubeDisconnect: () => request<YouTubeStatus>("/youtube", { method: "DELETE" }),
  contentItems: (platform?: string) => request<ContentItemRow[]>(`/content-items${qs(platform ? { platform } : {})}`),
  updateContentItem: (id: number, body: ContentItemUpdate) =>
    request<ContentItemRow>(`/content-items/${id}`, json("PATCH", body)),
  websiteStatus: () => request<WebsiteStatusDetail>("/website/status"),
  // Community members (Kiwi XP) admin
  membersOverview: () => request<import("../pages/members/types").MembersOverview>("/members-admin/overview"),
  membersList: (q: string, filter: string) => request<{ total: number; members: import("../pages/members/types").MemberRow[] }>(`/members-admin/members${qs({ q, filter })}`),
  memberDetail: (id: string) => request<import("../pages/members/types").MemberDetail>(`/members-admin/members/${id}`),
  memberAction: (id: string, action: string, body: object) =>
    request<import("../pages/members/types").MemberDetail>(`/members-admin/members/${id}/${action}`, json("POST", body)),
  memberRole: (id: string, role: "WORKER" | "MANAGER", grant: boolean) =>
    request<import("../pages/members/types").MemberRow>(`/members-admin/members/${id}/roles`, json("POST", { role, grant })),
  memberDelete: (id: string, reason: string) => request<{ deleted: boolean }>(`/members-admin/members/${id}`, json("DELETE", { reason })),
  reverseXp: (eventId: number, reason: string) =>
    request<import("../pages/members/types").MemberDetail>(`/members-admin/events/${eventId}/reverse`, json("POST", { reason })),
  resolveFlag: (flagId: number, status: "resolved" | "dismissed", resolution: string) =>
    request<import("../pages/members/types").MemberDetail>(`/members-admin/flags/${flagId}/resolve`, json("POST", { status, resolution })),
  membersConfig: () => request<import("../pages/members/types").MembersConfig>("/members-admin/config"),
  updateXpRule: (eventType: string, body: object) => request<import("../pages/members/types").MembersConfig>(`/members-admin/rules/${eventType}`, json("PATCH", body)),
  updateRank: (level: number, body: object) => request<import("../pages/members/types").MembersConfig>(`/members-admin/ranks/${level}`, json("PATCH", body)),
  updateEntitlement: (key: string, body: object) => request<import("../pages/members/types").MembersConfig>(`/members-admin/entitlements/${key}`, json("PATCH", body)),
  missionsAdmin: (includeExpired = false) =>
    request<{ missions: import("../pages/members/types").MissionRow[]; mission_daily_cap: number | null }>(
      `/members-admin/missions${qs({ include_expired: includeExpired ? "true" : "false" })}`),
  createMission: (body: object) => request<import("../pages/members/types").MissionRow>("/members-admin/missions", json("POST", body)),
  updateMission: (id: number, body: object) => request<import("../pages/members/types").MissionRow>(`/members-admin/missions/${id}`, json("PATCH", body)),
  generateMissions: () => request<{ created: number; expired: number }>("/members-admin/missions/generate", { method: "POST" }),
  exAuto: () => request<import("../pages/ExchangeAutoCard").AutoStatus>("/exchange/admin/auto"),
  setExAuto: (body: Partial<import("../pages/ExchangeAutoCard").AutoConfig>) =>
    request<import("../pages/ExchangeAutoCard").AutoStatus>("/exchange/admin/auto", json("PUT", body)),
  exAutoTick: () => request<{ enabled: boolean }>("/exchange/admin/auto/tick", { method: "POST" }),
  reputationSources: () => request<import("../pages/members/types").ReputationSources>("/members-admin/reputation"),
  updateReputation: (body: object) => request<import("../pages/members/types").ReputationSources>("/members-admin/reputation", json("PUT", body)),
  connectCombot: (api_key: string, chat_id: string) =>
    request<import("../pages/members/types").ReputationSources>("/members-admin/reputation/combot", json("PUT", { api_key, chat_id }), 60_000),
  disconnectCombot: () => request<import("../pages/members/types").ReputationSources>("/members-admin/reputation/combot", { method: "DELETE" }),
  syncCombot: () => request<import("../pages/members/types").ReputationSources & { result: Record<string, unknown> }>(
    "/members-admin/reputation/combot/sync", { method: "POST" }, 300_000),
  previewCombotImport: (body: { filename: string; format: "csv" | "json"; content: string; source_date?: string }) =>
    request<{ id: number; status: string; stats: Record<string, unknown> }>("/members-admin/reputation/import", json("POST", body), 120_000),
  applyCombotImport: (id: number) => request<import("../pages/members/types").ReputationSources>(`/members-admin/reputation/import/${id}/apply`, { method: "POST" }),
  discardCombotImport: (id: number) => request<import("../pages/members/types").ReputationSources>(`/members-admin/reputation/import/${id}/discard`, { method: "POST" }),
  saveXLoginClient: (client_id: string, client_secret?: string) =>
    request<unknown>("/members-admin/x-login", json("PUT", { client_id, ...(client_secret ? { client_secret } : {}) })),
  transferIdentity: (flagId: number, platform: string, to_member: string, reason: string) =>
    request<import("../pages/members/types").MemberDetail>(`/members-admin/flags/${flagId}/transfer-identity`, json("POST", { platform, to_member, reason })),
  membersAudit: () => request<{ entries: import("../pages/members/types").AuditEntry[] }>("/members-admin/audit"),
  memberAuthProviders: () => request<import("../website/members").Providers>("/members/auth/providers"),
  updateWebsiteSettings: (body: { sections?: Record<string, boolean>; links?: Record<string, string>; site_url?: string }) =>
    request<unknown>("/website/settings", json("PUT", body)),
  growthDashboard: (period: GrowthPeriod) => request<GrowthDashboard>(`/analytics/growth${qs({ period })}`),
  likePilot: (includeFiltered = false) => request<LikePilot>(`/x/likes${qs({ include_filtered: includeFiltered ? "true" : "false" })}`),
  proposeLike: (oppId: number) => request<Approval>(`/x/likes/${oppId}/propose`, { method: "POST" }),
  reviewSession: (size = 8) => request<{ items: FeedItem[]; filtered_out: number }>("/growth/review-session", json("POST", { size })),
  prepareItem: (key: string, direction?: string) =>
    request<Approval>(`/growth/prepare/${encodeURIComponent(key)}`, json("POST", direction ? { direction } : {}), AI_TIMEOUT_MS * 2),
  skipApproval: (id: number) => request<Approval>(`/growth/skip/${id}`, { method: "POST" }),
  learning: () => request<LearningSignal[]>("/growth/learning"),
  setLearning: (key: string, action: "accept" | "disable" | "reset" | "activate") =>
    request<LearningSignal[]>(`/growth/learning/${encodeURIComponent(key)}`, json("PUT", { action })),
  growthAnalytics: (days = 30) => request<GrowthAnalytics>(`/growth/analytics${qs({ days })}`),
  dailySummary: () => request<{ text: string; generated_at: string }>("/growth/daily-summary"),
  growthUsage: (days = 1) => request<GrowthUsage>(`/growth/usage${qs({ days })}`),
  creators: () => request<Creator[]>("/x/creators"),
  ignoreCreator: (profileId: number) => request<void>(`/x/creators/${profileId}/ignore`, { method: "POST" }),
  pilotStatus: () => request<PilotStatus>("/pilot-status", {}, 20_000),
  commandCenter: () => request<CommandCenter>("/command-center", {}, 60_000),
};

/** Plain-English explanation for each failure kind, used across the dashboard. */
export function explainError(err: unknown): { title: string; message: string; kind: ErrorKind } {
  if (!(err instanceof ApiError)) {
    return { title: "Something went wrong", message: String(err), kind: "error" };
  }
  const titles: Record<ErrorKind, string> = {
    backend_unavailable: "Backend unavailable",
    ai_unavailable: "AI unavailable",
    model_unavailable: "Model unavailable",
    timeout: "AI took too long",
    backend_slow: "Backend took too long",
    unauthorized: "Signed out",
    paused: "Agent paused",
    validation: "Please check your input",
    not_found: "Not found",
    error: "Something went wrong",
  };
  return { title: titles[err.kind], message: err.message, kind: err.kind };
}
