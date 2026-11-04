// Purpose: top-level screen. Handles sign-in, submit / review actions, shows the review queue, the case decision panel,
// the review timeline (reminders, escalations, expiry) and the decisions table.
import { useEffect, useState } from "react";
import type { User } from "oidc-client-ts";
import { currentUser, groupsOf, login, logout } from "./auth";
import { AgentResult, Application, ReviewEvent, ReviewPayload, ReviewTask, getCase, getTimeline, listDecisions, listReviews, sendReview, submitApplication } from "./api";
import { ReviewQueue } from "./components/ReviewQueue";
import { ApplicationForm } from "./components/ApplicationForm";
import { DecisionPanel } from "./components/DecisionPanel";
import { DecisionsTable } from "./components/DecisionsTable";
import { ReviewTimeline } from "./components/ReviewTimeline";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [decisions, setDecisions] = useState<Array<Record<string, string>>>([]);
  const [reviews, setReviews] = useState<ReviewTask[]>([]);
  const [timeline, setTimeline] = useState<ReviewEvent[]>([]);
  const [error, setError] = useState("");

  useEffect(() => { currentUser().then(setUser).catch((e) => setError(String(e))).finally(() => setReady(true)); }, []);
  const refresh = () => {
    listDecisions().then((r) => setDecisions(r.items)).catch(() => undefined);
    listReviews().then((r) => setReviews(r.items)).catch(() => undefined);
  };
  useEffect(() => { if (user) refresh(); }, [user]);

  const run = async (fn: () => Promise<AgentResult>) => {
    setBusy(true); setError("");
    try {
      const r = await fn();
      setResult(r); refresh();
      getTimeline(r.application_id).then((t) => setTimeline(t.items)).catch(() => setTimeline([]));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };

  const openTask = (t: ReviewTask) => run(() => getCase(t.session_id));

  if (!ready) return <p className="center">Loading…</p>;
  if (!user) return <div className="center"><h1>UW-Agent</h1><p>Agentic SME credit underwriting</p><button onClick={login}>Sign in</button>{error && <p className="err">{error}</p>}</div>;

  const canApprove = groupsOf(user).includes("underwriters");
  return (
    <>
      <header><b>UW-Agent</b><span>{String(user.profile.email)} · {groupsOf(user).join(", ") || "no group"}</span><button className="link" onClick={logout}>Sign out</button></header>
      <main>
        <ApplicationForm busy={busy} onSubmit={(a: Application) => run(() => submitApplication(a))} />
        {error && <p className="err">{error}</p>}
        <ReviewQueue items={reviews} onOpen={openTask} />
        {result && <ReviewTimeline items={timeline} />}
        {result && <DecisionPanel result={result} canReview={canApprove} busy={busy}
          onReview={(p: ReviewPayload) => run(() => sendReview(result.session_id, p))} />}
        <DecisionsTable items={decisions} />
      </main>
    </>
  );
}
