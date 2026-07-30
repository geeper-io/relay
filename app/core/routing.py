"""Deployment aliases and versioned capability-policy routing."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.core.exceptions import ModelNotAllowedError


@dataclass(frozen=True)
class RoutingDecision:
    requested_model: str
    model: str
    deployment: str | None
    policy_version: str
    required_capabilities: tuple[str, ...]
    fallback_models: tuple[str, ...] = ()

    def audit_metadata(self) -> dict:
        return {
            "requested_model": self.requested_model,
            "deployment": self.deployment,
            "policy_version": self.policy_version,
            "required_capabilities": list(self.required_capabilities),
        }


class ModelRouter:
    """Resolve logical deployments while enforcing a versioned policy snapshot."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def route(
        self,
        requested_model: str,
        *,
        required_capabilities: set[str] | None = None,
        team_id: str | None = None,
    ) -> RoutingDecision:
        required = set(required_capabilities or ())
        version = self._settings.active_policy_version
        policy = dict(self._settings.routing_policies.get(version, {}))
        if self._settings.routing_policies and version not in self._settings.routing_policies:
            raise ModelNotAllowedError(f"Routing policy version '{version}' is not configured")

        effective_policy = self._effective_policy(policy, team_id)
        denied = set(effective_policy.get("denied_capabilities", []))
        blocked = sorted(required & denied)
        if blocked:
            raise ModelNotAllowedError(f"Policy '{version}' denies required capabilities: {', '.join(blocked)}")

        allowed_caps = set(effective_policy.get("allowed_capabilities", []))
        outside_allowlist = sorted(required - allowed_caps) if allowed_caps else []
        if outside_allowlist:
            raise ModelNotAllowedError(
                f"Policy '{version}' does not allow capabilities: {', '.join(outside_allowlist)}"
            )

        candidate = requested_model
        if candidate in {"", "auto"}:
            candidate = self._select_automatic_deployment(required, effective_policy)

        deployment_name: str | None = None
        fallback_models: tuple[str, ...] = ()
        deployments = self._settings.deployments
        if candidate in deployments:
            deployment_name = candidate
            deployment = deployments[candidate]
            self._check_deployment_allowed(deployment_name, effective_policy)
            self._check_capabilities(deployment_name, deployment, required)
            model = str(deployment.get("model") or "")
            if not model:
                raise ModelNotAllowedError(f"Deployment '{deployment_name}' has no model")
            fallback_models = tuple(str(item) for item in deployment.get("fallback_models", []))
        else:
            model = self._settings.model_aliases.get(candidate, candidate)
            if self._settings.require_declared_capabilities and required:
                raise ModelNotAllowedError(
                    f"Model '{candidate}' is not a declared deployment; capabilities cannot be verified"
                )

        model = self._ensure_allowed_model(model, candidate)
        return RoutingDecision(
            requested_model=requested_model,
            model=model,
            deployment=deployment_name,
            policy_version=version,
            required_capabilities=tuple(sorted(required)),
            fallback_models=fallback_models,
        )

    def _effective_policy(self, policy: dict, team_id: str | None) -> dict:
        effective = {key: value for key, value in policy.items() if key != "team_overrides"}
        if team_id:
            override = policy.get("team_overrides", {}).get(team_id, {})
            effective.update(override)
        return effective

    def _select_automatic_deployment(self, required: set[str], policy: dict) -> str:
        capability_routes = policy.get("capability_routes", {})
        for capability in sorted(required):
            if capability in capability_routes:
                return str(capability_routes[capability])

        default = policy.get("default_deployment")
        if default:
            return str(default)

        for name in policy.get("deployment_order", self._settings.deployments.keys()):
            deployment = self._settings.deployments.get(str(name))
            if deployment and self._supports(deployment, required):
                return str(name)

        if self._settings.default_model:
            return self._settings.default_model
        raise ModelNotAllowedError("No deployment satisfies the requested capabilities")

    def _check_deployment_allowed(self, name: str, policy: dict) -> None:
        allowed = set(policy.get("allowed_deployments", []))
        if allowed and name not in allowed:
            raise ModelNotAllowedError(f"Policy does not allow deployment '{name}'")

    def _check_capabilities(self, name: str, deployment: dict, required: set[str]) -> None:
        declared = set(deployment.get("capabilities", []))
        if not declared and not self._settings.require_declared_capabilities:
            return
        missing = sorted(required - declared)
        if missing:
            raise ModelNotAllowedError(f"Deployment '{name}' does not support: {', '.join(missing)}")

    @staticmethod
    def _supports(deployment: dict, required: set[str]) -> bool:
        declared = set(deployment.get("capabilities", []))
        return not required or required <= declared

    def _ensure_allowed_model(self, model: str, requested: str) -> str:
        allowed = self._settings.allowed_models
        if not allowed or model in allowed:
            return model
        for allowed_model in allowed:
            if allowed_model.split("/", 1)[-1] in {model, requested}:
                return allowed_model
        raise ModelNotAllowedError(
            f"Model '{requested}' is not available. Allowed: " + ", ".join(item.split("/", 1)[-1] for item in allowed)
        )
