import pytest

from app.config import Settings
from app.core.exceptions import ModelNotAllowedError
from app.core.routing import ModelRouter


def _settings(**overrides) -> Settings:
    values = {
        "llm__default_model": "general",
        "llm__allowed_models": ["openai/gpt-main", "openai/gpt-vision", "anthropic/claude-backup"],
        "llm__deployments": {
            "general": {
                "model": "openai/gpt-main",
                "capabilities": ["chat", "responses", "streaming", "tools"],
                "fallback_models": ["anthropic/claude-backup"],
            },
            "vision": {
                "model": "openai/gpt-vision",
                "capabilities": ["chat", "responses", "streaming", "vision"],
            },
        },
        "routing__active_policy_version": "2026-07-30",
        "routing__require_declared_capabilities": True,
        "routing__policies": {
            "2026-07-30": {
                "default_deployment": "general",
                "allowed_capabilities": ["chat", "responses", "streaming", "tools", "vision"],
                "allowed_deployments": ["general", "vision"],
                "capability_routes": {"vision": "vision"},
            }
        },
    }
    values.update(overrides)
    return Settings(**values)


def test_deployment_alias_resolves_model_and_fallbacks():
    decision = ModelRouter(_settings()).route(
        "general",
        required_capabilities={"responses", "tools"},
        team_id="team-1",
    )
    assert decision.model == "openai/gpt-main"
    assert decision.deployment == "general"
    assert decision.policy_version == "2026-07-30"
    assert decision.fallback_models == ("anthropic/claude-backup",)


def test_auto_route_uses_capability_route():
    decision = ModelRouter(_settings()).route("auto", required_capabilities={"responses", "vision"})
    assert decision.deployment == "vision"
    assert decision.model == "openai/gpt-vision"


def test_deployment_rejects_missing_capability():
    with pytest.raises(ModelNotAllowedError, match="does not support: vision"):
        ModelRouter(_settings()).route("general", required_capabilities={"responses", "vision"})


def test_versioned_policy_can_deny_capability():
    settings = _settings(
        routing__policies={
            "2026-07-30": {
                "default_deployment": "general",
                "denied_capabilities": ["tools"],
            }
        }
    )
    with pytest.raises(ModelNotAllowedError, match="denies required capabilities: tools"):
        ModelRouter(settings).route("general", required_capabilities={"tools"})


def test_team_override_can_restrict_deployments():
    settings = _settings(
        routing__policies={
            "2026-07-30": {
                "allowed_deployments": ["general", "vision"],
                "team_overrides": {"team-locked": {"allowed_deployments": ["general"]}},
            }
        }
    )
    with pytest.raises(ModelNotAllowedError, match="does not allow deployment 'vision'"):
        ModelRouter(settings).route("vision", required_capabilities={"vision"}, team_id="team-locked")


def test_direct_models_remain_backward_compatible_without_strict_capabilities():
    settings = _settings(
        llm__default_model="openai/gpt-main",
        llm__deployments={},
        routing__active_policy_version="default",
        routing__require_declared_capabilities=False,
        routing__policies={},
    )
    decision = ModelRouter(settings).route("gpt-main", required_capabilities={"chat"})
    assert decision.model == "openai/gpt-main"
    assert decision.deployment is None
