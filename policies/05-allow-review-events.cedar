// Review tasks / feedback / lifecycle events may be written by callers with the agent.invoke scope,
// and by the approval workflow principal (system.resume) so it can mark an expired task.
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"review-queue___record_review_event",
  resource == AgentCore::Gateway::"${GATEWAY_ARN}"
) when {
  principal.hasTag("scope") &&
  (principal.getTag("scope") like "*underwriting/agent.invoke*" || principal.getTag("scope") like "*underwriting/system.resume*")
};
