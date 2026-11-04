// Defence in depth: whatever the agent or the workflow does, the system principal can never approve or decline.
forbid(
  principal,
  action == AgentCore::Action::"decision-recorder___record_decision",
  resource == AgentCore::Gateway::"${GATEWAY_ARN}"
) when {
  principal.hasTag("scope") && principal.getTag("scope") like "*underwriting/system.resume*" &&
  context.input.decision != "REFERRED"
};
