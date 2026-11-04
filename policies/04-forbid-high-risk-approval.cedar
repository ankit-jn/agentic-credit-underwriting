// A HIGH-risk case can never be approved through the decision tool (needs credit committee, outside this agent).
forbid(
  principal,
  action == AgentCore::Action::"decision-recorder___record_decision",
  resource == AgentCore::Gateway::"${GATEWAY_ARN}"
) when {
  context.input.decision == "APPROVED" && context.input.risk_band == "HIGH"
};
