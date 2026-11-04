// Purpose: per-case timeline built from EventBridge workflow events (requested, reminders, escalations, decision, expiry).
import type { ReviewEvent } from "../api";

const LABEL: Record<string, string> = {
  ReviewRequested: "Review requested",
  ReviewReminder: "Reminder sent",
  ReviewEscalated: "Escalated",
  ReviewCompleted: "Reviewer responded",
  ReviewExpired: "Expired: no decision, referred",
  ReviewExpireFailed: "Expiry failed: needs a person",
  ReviewWorkflowFailed: "Workflow step failed",
  ComplianceReferred: "Referred to Compliance",
};

function describe(e: ReviewEvent): string {
  const d = e.detail as Record<string, string | number | Record<string, string>>;
  switch (e.type) {
    case "ReviewRequested": return `Tier ${d.tier}, due ${new Date(String(d.sla_due)).toLocaleString()}`;
    case "ReviewReminder": return `Audience: ${d.audience}`;
    case "ReviewEscalated": return `${d.from_tier} -> ${d.to_tier} (level ${d.level}, ${d.hours_overdue} h overdue), notified ${d.audience}`;
    case "ReviewCompleted": return `${(d.outcome as Record<string, string>)?.action ?? ""} by ${(d.outcome as Record<string, string>)?.reviewer ?? ""}`;
    default: return "";
  }
}

export function ReviewTimeline({ items }: { items: ReviewEvent[] }) {
  if (!items.length) return null;
  return (
    <div className="card">
      <h2>Review timeline</h2>
      <ol>
        {items.map((e, i) => (
          <li key={i}>
            <b>{LABEL[e.type] ?? e.type}</b> <span className="hint">{new Date(e.time).toLocaleString()}</span>
            {describe(e) && <div>{describe(e)}</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}
