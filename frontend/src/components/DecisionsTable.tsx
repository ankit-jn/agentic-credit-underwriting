// Purpose: read-only list of recorded decisions (audit view).
export function DecisionsTable({ items }: { items: Array<Record<string, string>> }) {
  return (
    <div className="card">
      <h2>Recent decisions</h2>
      <table>
        <thead><tr><th>Application</th><th>Applicant</th><th>Amount</th><th>Risk</th><th>Decision</th><th>By</th><th>When</th></tr></thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.application_id}>
              <td>{i.application_id}</td><td>{i.applicant_id}</td><td>{Number(i.amount).toLocaleString()}</td>
              <td>{i.risk_band}</td><td>{i.decision}</td><td>{i.decided_by}</td><td>{i.decided_at?.slice(0, 16)}</td>
            </tr>
          ))}
          {!items.length && <tr><td colSpan={7}>No decisions yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
