// Purpose: typed client for the BFF API. Adds the user's bearer token to every call. Types mirror the BFF responses
// (ReviewTask includes escalation fields; ReviewEvent is a workflow timeline entry).
import { userManager } from "./auth";

export interface Application {
  application_id: string; applicant_id: string; legal_name: string;
  requested_amount: number; tenor_months: number; purpose: string;
}
export type ReviewAction = "APPROVE" | "DECLINE" | "REQUEST_INFO";
export interface AgentResult {
  status: "PENDING_REVIEW" | "COMPLETED" | "REFERRED"; session_id: string; application_id: string;
  decision?: string; risk_band?: string; recommendation?: string; memo?: string; cited_sources?: string[];
  confidence?: number; confidence_reasons?: string[]; review_tier?: string; review_round?: number; qa_sampled?: boolean;
  kyc?: Record<string, unknown>; bureau?: Record<string, unknown>; cashflow?: Record<string, unknown>;
  trace?: string[]; pending?: Array<Record<string, unknown>>; error?: string;
}
export interface ReviewTask {
  application_id: string; tier: string; priority: number; sla_due: string; created_at: string;
  confidence: string; reasons: string; summary: string; session_id: string; review_round?: number;
  // durable approval workflow (Step Functions): escalation ladder state
  status?: string; escalation_level?: number; reminders_sent?: number; original_tier?: string; workflow_state?: string; workflow_deadline?: string;
}
export interface ReviewEvent { time: string; type: string; detail: Record<string, unknown> }
export interface ReviewPayload { action_type: ReviewAction; reason_codes: string[]; comment: string; additional_info?: string }

async function call<T>(path: string, init: RequestInit & { session?: string } = {}): Promise<T> {
  const user = await userManager.getUser();
  if (!user) throw new Error("Not signed in");
  const res = await fetch(`${import.meta.env.VITE_API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${user.access_token}`,
      ...(init.session ? { "x-session-id": init.session } : {}),
    },
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export const submitApplication = (application: Application) =>
  call<AgentResult>("/agent", { method: "POST", body: JSON.stringify({ action: "submit", application }) });
export const getCase = (session: string) =>
  call<AgentResult>("/agent", { method: "POST", session, body: JSON.stringify({ action: "get" }) });
export const sendReview = (session: string, p: ReviewPayload) =>
  call<AgentResult>("/agent", { method: "POST", session, body: JSON.stringify({ action: "review", ...p }) });
export const listDecisions = () => call<{ items: Array<Record<string, string>> }>("/applications");
export const listReviews = () => call<{ items: ReviewTask[] }>("/reviews");
export const getTimeline = (applicationId: string) =>
  call<{ items: ReviewEvent[] }>(`/reviews/${encodeURIComponent(applicationId)}/timeline`);
