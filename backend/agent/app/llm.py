"""Factory for the Bedrock chat model used for risk assessment (temperature 0, Guardrail attached when configured)."""
from langchain_aws import ChatBedrockConverse

from .config import get_settings


def get_llm():
    """Return a Bedrock Converse chat model; attaches the Guardrail (PII redaction, protected-attribute blocking) when its id is set.
    """
    s = get_settings()
    kwargs = {}
    if s.guardrail_id:
        kwargs["guardrail_config"] = {"guardrailIdentifier": s.guardrail_id, "guardrailVersion": s.guardrail_version}
    return ChatBedrockConverse(model=s.model_id, region_name=s.region, temperature=0, max_tokens=1500, **kwargs)
