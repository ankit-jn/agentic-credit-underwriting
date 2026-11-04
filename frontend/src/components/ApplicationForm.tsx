// Purpose: form an RM uses to submit a loan application to the agent.
import { useState } from "react";
import type { Application } from "../api";

const SAMPLES: Record<string, Partial<Application>> = {
  "APP-1001": { legal_name: "Sunrise Agro Processors Pvt Ltd", requested_amount: 150000 },
  "APP-1002": { legal_name: "Delta Logistics LLP", requested_amount: 600000 },
  "APP-1003": { legal_name: "Blocked Trading Co", requested_amount: 100000 },
  "APP-1004": { legal_name: "Nova MedCare Services", requested_amount: 300000 },
};

export function ApplicationForm({ busy, onSubmit }: { busy: boolean; onSubmit: (a: Application) => void }) {
  const [applicantId, setApplicantId] = useState("APP-1001");
  const [f, setF] = useState({ ...SAMPLES["APP-1001"], tenor_months: 24, purpose: "Working capital" } as Application);

  const pick = (id: string) => {
    setApplicantId(id);
    setF({ ...f, ...SAMPLES[id] } as Application);
  };
  return (
    <form className="card" onSubmit={(e) => { e.preventDefault(); onSubmit({ ...f, applicant_id: applicantId, application_id: `LN-${Date.now()}` }); }}>
      <h2>New application</h2>
      <label>Applicant (demo data)
        <select value={applicantId} onChange={(e) => pick(e.target.value)}>
          {Object.keys(SAMPLES).map((id) => <option key={id}>{id}</option>)}
        </select>
      </label>
      <label>Legal name<input value={f.legal_name} onChange={(e) => setF({ ...f, legal_name: e.target.value })} required /></label>
      <label>Requested amount<input type="number" min={1000} value={f.requested_amount} onChange={(e) => setF({ ...f, requested_amount: +e.target.value })} required /></label>
      <label>Tenor (months)<input type="number" min={3} max={84} value={f.tenor_months} onChange={(e) => setF({ ...f, tenor_months: +e.target.value })} required /></label>
      <label>Purpose<input value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} /></label>
      <button disabled={busy}>{busy ? "Agent is working…" : "Run underwriting agent"}</button>
    </form>
  );
}
