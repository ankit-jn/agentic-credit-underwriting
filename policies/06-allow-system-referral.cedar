// The approval workflow principal (Step Functions, via the agent) may record ONE kind of decision: REFERRED.
// It is allowed at any ticket size, so a case above the hard cap that nobody picked up can still be closed safely.
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"decision-recorder___record_decision",
  resource == AgentCore::Gateway::"${GATEWAY_ARN}"
) when {
  principal.hasTag("scope") && principal.getTag("scope") like "*underwriting/system.resume*" &&
  context.input.decision == "REFERRED"
};
