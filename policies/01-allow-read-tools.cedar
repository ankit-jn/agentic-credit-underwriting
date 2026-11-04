// Any caller holding the agent.invoke scope may use the read-only evidence tools.
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"kyc-screening___screen_entity",
    AgentCore::Action::"cashflow-analyzer___analyze_cashflow",
    AgentCore::Action::"credit-bureau-api___getCreditScore"
  ],
  resource == AgentCore::Gateway::"${GATEWAY_ARN}"
) when {
  principal.hasTag("scope") && principal.getTag("scope") like "*underwriting/agent.invoke*"
};
