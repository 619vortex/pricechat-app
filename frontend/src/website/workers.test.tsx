import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RankKiwi, WORKER_OVERLAY } from "./RankKiwi";
import { ManagerTab, WorkerTab } from "./workers";

const day = (verified: number, status: string) => ({ day: "2026-10-16", target: 5, verified, status, report_id: null, reason: null });
const ME = (verified: number, status: string) => ({
  roles: ["WORKER"], assignment: { id: 1, job_type: "TELEGRAM_INVITES", daily_target: 5, since: "2026-10-16" }, today: day(verified, status), timezone: "UTC",
  links: { bot: "https://t.me/cryptopricechatbot?start=wk_abc", chat: null, chat_unavailable: "not admin" },
  totals: { submissions: 6, verified, approved: 0, pending: 1, rejected: 0, engagement: 42 },
  submissions: [{ id: 9, username: "friend_one", tg_user_id: 101, source: "form", status: "VERIFIED", submitted_at: "2026-10-16T10:00:00Z", verified_at: "2026-10-16T10:05:00Z",
    joined_at: "2026-10-16T10:01:00Z", engagement: 7, engagement_at: null, still_member: true, reject_reason: null, report_id: null, note: null }], page: 1, pages: 1 });
let calls: [string, string | undefined][] = [];
let me = ME(2, "IN_PROGRESS");

beforeEach(() => {
  calls = [];
  me = ME(2, "IN_PROGRESS");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([`${init?.method ?? "GET"} ${url}`, init?.body as string | undefined]);
    const body = url.includes("/workers/me") ? me : url.includes("/workers/submissions") ? { ...me.submissions[0], username: "new_friend" }
      : url.includes("/workers/reports/submit") ? { id: 1 } : url.includes("/workers/reports") ? { items: [], pages: 1 }
      : url.includes("/manager/reports/7/decision") ? { id: 7, worker: { id: 2, username: "van" }, day: "2026-10-16", target: 5, verified_count: 5, status: "APPROVED",
          submitted_at: "", decided_at: "2026-10-16T12:00:00Z", decided_by: "manager:boss", reason: null, submissions: [], reviews: [] }
      : url.includes("/manager/reports/7") ? { id: 7, worker: { id: 2, username: "van" }, day: "2026-10-16", target: 5, verified_count: 5, status: "PENDING", submitted_at: "",
          decided_at: null, decided_by: null, reason: null, engagement_total: 12, submissions: [{ ...me.submissions[0], evidence: {} }], reviews: [] }
      : url.includes("/workers/manager") ? { workers: [{ id: 2, username: "van", active_worker: true, assignment: { daily_target: 5 }, today: day(5, "PENDING_APPROVAL"), last14: {} }],
          reports: [{ id: 7, worker: { id: 2, username: "van" }, day: "2026-10-16", target: 5, verified_count: 5, status: "PENDING", submitted_at: "", decided_at: null,
            decided_by: null, reason: null }], page: 1, pages: 1 } : {};
    return new Response(JSON.stringify(body), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Worker System", () => {
  it("the worker tab shows the job, today's progress, totals and engagement per invite; submits one username", async () => {
    render(<WorkerTab />);
    await waitFor(() => expect(screen.getByText("2 / 5")).toBeTruthy());
    expect(screen.getByText("In progress")).toBeTruthy();
    expect(screen.getByText("💬 7")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.queryByText(/Submit today's report/)).toBeNull();  // not until the target is reached
    fireEvent.change(screen.getByLabelText("Telegram username"), { target: { value: "@New_Friend" } });
    fireEvent.click(screen.getByText("Submit"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("@new_friend submitted"));
    expect(calls.find(([c]) => c === "POST /api/members/workers/submissions")?.[1]).toBe(JSON.stringify({ username: "@New_Friend" }));
  });

  it("at the target the report can be sent for approval", async () => {
    me = ME(5, "READY_TO_SUBMIT");
    render(<WorkerTab />);
    await waitFor(() => screen.getByText(/Submit today's report/));
    fireEvent.click(screen.getByText(/Submit today's report/));
    await waitFor(() => expect(calls.some(([c]) => c === "POST /api/members/workers/reports/submit")).toBe(true));
  });

  it("a manager reviews the evidence and approves", async () => {
    render(<ManagerTab />);
    await waitFor(() => screen.getAllByText(/van/));
    fireEvent.click(screen.getByRole("button", { name: /van · 2026-10-16/ }));
    await waitFor(() => screen.getByText("Approve"));
    expect(screen.getByText("101")).toBeTruthy();  // the numeric Telegram id, the evidence
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(calls.some(([c]) => c === "POST /api/members/workers/manager/reports/7/decision")).toBe(true));
    await waitFor(() => expect(screen.getByText(/Decisions are final/)).toBeTruthy());
  });

  it("a worker's Kiwi wears the gear; everyone else's doesn't", () => {
    const art = { treatment: "none", overlay: null, placeholder: null, slot: null } as never;
    const { container, rerender } = render(<RankKiwi art={art} size={100} worker />);
    expect(container.querySelector(".rk-overlay")?.getAttribute("src")).toBe(WORKER_OVERLAY);
    rerender(<RankKiwi art={art} size={100} />);
    expect(container.querySelector(".rk-overlay")).toBeNull();
  });
});
