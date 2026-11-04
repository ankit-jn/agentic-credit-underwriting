// Defence in depth: an explicit forbid always wins over any permit.
forbid(
  principal,
  action == AgentCore::Action::"decision-recorder___record_decision",
  resource == AgentCore::Gateway::"${GATEWAY_ARN}"
) when {
  context.input.decision == "APPROVED" && context.input.amount > 1000000
};
