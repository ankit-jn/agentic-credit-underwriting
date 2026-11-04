environment      = "dev"
region           = "us-east-1"
bedrock_model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0" # replace with a model/profile enabled in your account
enforce_four_eyes = false # single test user in dev
qa_sample_rate    = 1.0   # review every auto-approval while the model is new
workflow_time_scale = 0.01 # 24h SLA becomes ~15 min: lets you watch reminder -> escalation -> expiry end to end
