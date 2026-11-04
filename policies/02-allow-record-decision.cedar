// Recording a decision needs the decision.write scope and a ticket inside the bank-wide hard cap.
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"decision-recorder___record_decision",
  resource == AgentCore::Gateway::"${GATEWAY_ARN}"
) when {
  principal.hasTag("scope") && principal.getTag("scope") like "*underwriting/decision.write*" &&
  context.input.amount <= 1000000
};
