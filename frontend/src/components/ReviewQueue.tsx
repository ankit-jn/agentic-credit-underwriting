// Purpose: the underwriter's work queue, ordered by SLA, with tier, original tier, escalation level and reminder status.
import type { ReviewTask } from "../api";

const minutesLeft = (due: string) => Math.round((new Date(due).getTime() - Date.now()) / 60000);

export function ReviewQueue({ items, onOpen }: { items: ReviewTask[]; onOpen: (t: ReviewTask) => void }) {
  return (
    <div className="card">
      <h2>Review queue <span className="badge">{items.length}</span></h2>
      <table>
        <thead><tr><th>Application</th><th>Tier</th><th>Confidence</th><th>SLA</th><th>Escalation</th><th></th></tr></thead>
        <tbody>
          {items.map((t) => {
            const left = minutesLeft(t.sla_due);
            return (
              <tr key={t.application_id}>
                <td>{t.application_id}</td>
                <td>
                  <span className={`badge ${t.tier === "SENIOR" || t.tier === "COMMITTEE" ? "high" : ""}`}>{t.tier}</span>
                  {t.original_tier && t.original_tier !== t.tier && <span className="hint"> (was {t.original_tier})</span>}
                </td>
                <td>{Number(t.confidence).toFixed(2)}</td>
                <td className={left < 0 ? "err" : ""}>{left < 0 ? `overdue ${-left} min` : `${left} min left`}</td>
                <td>
                  {Number(t.escalation_level ?? 0) > 0
                    ? <span className="badge high">L{t.escalation_level}</span>
                    : (Number(t.reminders_sent ?? 0) > 0 ? <span className="badge medium">reminded</span> : "–")}
                </td>
                <td><button className="link" onClick={() => onOpen(t)} disabled={!t.session_id}>Open</button></td>
              </tr>
            );
          })}
          {!items.length && <tr><td colSpan={6}>Nothing waiting for review.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
