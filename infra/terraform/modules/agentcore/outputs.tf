# Outputs of the agentcore module (runtime, gateway, memory, knowledge base ids).
output "runtime_arn" { value = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn }
output "gateway_url" { value = aws_bedrockagentcore_gateway.this.gateway_url }
output "gateway_arn" { value = aws_bedrockagentcore_gateway.this.gateway_arn }
output "memory_id" { value = aws_bedrockagentcore_memory.this.id }
output "policy_engine_id" { value = aws_bedrockagentcore_policy_engine.this.policy_engine_id }
