// Purpose: shows the agent's recommendation, memo, confidence and reasons, evidence and policy citations;
// lets an underwriter approve / decline (reason codes required) or request more information.
import { useState } from "react";
import type { AgentResult, ReviewPayload } from "../api";

const badge = (s?: string | number) => <span className={`badge ${String(s ?? "").toLowerCase()}`}>{s}</span>;

const REASON_CODES = [
  "DSCR_TOO_LOW", "BUREAU_SCORE_LOW", "CASHFLOW_VOLATILE", "AGENT_MISREAD_EVIDENCE", "POLICY_EXCEPTION_GRANTED",
  "STRONG_MITIGANTS", "SECTOR_RESTRICTED", "DOCUMENTS_INSUFFICIENT", "OTHER",
];

export function DecisionPanel({ result, canReview, busy, onReview }: {
  result: AgentResult; canReview: boolean; busy: boolean; onReview: (p: ReviewPayload) => void;
}) {
  const [codes, setCodes] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [info, setInfo] = useState("");
  const toggle = (c: string) => setCodes((x) => (x.includes(c) ? x.filter((y) => y !== c) : [...x, c]));
  const pending = result.status === "PENDING_REVIEW";
  const conf = result.confidence ?? 0;

  return (
    <div className="card">
      <h2>Result {badge(result.status)}</h2>
      {result.error && <p className="err">{result.error}</p>}
      <p>
        <b>Agent recommends:</b> {result.recommendation} · <b>Risk:</b> {badge(result.risk_band)} · <b>Decision:</b> {result.decision ?? "pending"}
      </p>
      <p>
        <b>Confidence:</b> <span className={`badge ${conf >= 0.85 ? "low" : conf >= 0.6 ? "medium" : "high"}`}>{conf.toFixed(2)}</span>
        {" "}<b>Route:</b> {badge(result.review_tier)}{result.qa_sampled && <> {badge("QA sample")}</>}
      </p>
      {!!result.confidence_reasons?.length && (
        <><h3>Why a human is needed / what lowered confidence</h3><ul>{result.confidence_reasons.map((r) => <li key={r}>{r}</li>)}</ul></>
      )}
      <h3>Credit memo</h3>
      <p className="memo">{result.memo}</p>
      {!!result.cited_sources?.length && (<><h3>Policy sources</h3><ul>{result.cited_sources.map((s) => <li key={s}><code>{s}</code></li>)}</ul></>)}
      <details><summary>Evidence</summary>
        <pre>{JSON.stringify({ kyc: result.kyc, bureau: result.bureau, cashflow: result.cashflow }, null, 2)}</pre>
      </details>
      <details><summary>Agent trace</summary><ol>{result.trace?.map((t, i) => <li key={i}>{t}</li>)}</ol></details>

      {pending && !canReview && <p className="hint">Awaiting a reviewer in group <code>underwriters</code>.</p>}
      {pending && canReview && (
        <div className="approve">
          <h3>Your review (round {result.review_round ?? 0})</h3>
          <p className="hint">Reason codes are required to approve or decline: they become labelled feedback for evaluation and calibration.</p>
          <div className="chips">
            {REASON_CODES.map((c) => (
              <label key={c} className={`chip ${codes.includes(c) ? "on" : ""}`}>
                <input type="checkbox" checked={codes.includes(c)} onChange={() => toggle(c)} /> {c}
              </label>
            ))}
          </div>
          <textarea placeholder="Comment (kept in audit log)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <textarea placeholder="Only for 'Request info': what should the agent consider / what was missing?" value={info} onChange={(e) => setInfo(e.target.value)} />
          <button disabled={busy || !codes.length} onClick={() => onReview({ action_type: "APPROVE", reason_codes: codes, comment })}>Approve</button>
          <button disabled={busy || !codes.length} className="danger" onClick={() => onReview({ action_type: "DECLINE", reason_codes: codes, comment })}>Decline</button>
          <button disabled={busy || !info.trim()} className="secondary" onClick={() => onReview({ action_type: "REQUEST_INFO", reason_codes: codes, comment, additional_info: info })}>Request info</button>
        </div>
      )}
    </div>
  );
}
