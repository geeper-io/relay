"""Versioned MCP tool authorization and argument constraints."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.config import Settings
from app.core.auth import ResolvedIdentity
from app.core.exceptions import AuthorizationError

MCPAction = Literal["allow", "require_approval", "deny"]
_ACTIONS = {"allow", "require_approval", "deny"}


@dataclass(frozen=True)
class MCPPolicyDecision:
    action: MCPAction
    policy_version: str
    reason: str
    rule_name: str | None = None
    constraints: dict[str, Any] | None = None
    grant: dict[str, Any] | None = None


class MCPPolicyEngine:
    """Evaluate first-match rules from an immutable configured policy version."""

    def __init__(
        self,
        settings: Settings,
        *,
        policy_version: str | None = None,
        policy_document: dict[str, Any] | None = None,
    ):
        self._settings = settings
        self._explicit_policy = policy_version is not None or policy_document is not None
        self._policy_version = policy_version or settings.mcp_active_policy_version
        self._policy_document = dict(policy_document or {})

    def authorize(
        self,
        identity: ResolvedIdentity,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPPolicyDecision:
        if not self._settings.mcp_enabled:
            return self._decision("deny", "MCP gateway is disabled")
        if server not in self._settings.mcp_servers:
            return self._decision("deny", "MCP server is not registered")
        if not has_tool_scope(identity, server, tool):
            return self._decision("deny", "API key is not scoped for this MCP tool")

        version = self._policy_version
        if self._explicit_policy:
            policy = self._policy_document
        else:
            policies = self._settings.mcp_policies
            if policies and version not in policies:
                return self._decision("deny", f"MCP policy version '{version}' is not configured")
            policy = policies.get(version, {})
        for index, rule in enumerate(policy.get("rules", [])):
            if not _matches(rule, identity, server, tool):
                continue
            action = str(rule.get("action", "deny"))
            if action not in _ACTIONS:
                return self._decision("deny", f"MCP policy rule has invalid action '{action}'")
            required = [str(scope) for scope in rule.get("required_scopes", [])]
            missing = [scope for scope in required if not identity.has_scope(scope)]
            if missing:
                return self._decision("deny", "Missing policy scopes: " + ", ".join(missing))
            constraints = dict(rule.get("constraints", {}))
            grant = dict(rule.get("grant", {})) or None
            if arguments is not None:
                try:
                    validate_argument_constraints(arguments, constraints)
                except AuthorizationError as exc:
                    return self._decision("deny", exc.message, str(rule.get("name") or f"rule-{index}"), constraints)
            return self._decision(
                action,
                str(rule.get("reason") or f"Matched MCP policy rule {index}"),
                str(rule.get("name") or f"rule-{index}"),
                constraints,
                grant,
            )

        default_action = str(policy.get("default_action", "deny"))
        if default_action not in _ACTIONS:
            default_action = "deny"
        return self._decision(default_action, "MCP policy default action")

    def _decision(
        self,
        action: str,
        reason: str,
        rule_name: str | None = None,
        constraints: dict[str, Any] | None = None,
        grant: dict[str, Any] | None = None,
    ) -> MCPPolicyDecision:
        return MCPPolicyDecision(
            action=action,  # type: ignore[arg-type]
            policy_version=self._policy_version,
            reason=reason,
            rule_name=rule_name,
            constraints=constraints,
            grant=grant,
        )


def has_any_mcp_scope(identity: ResolvedIdentity) -> bool:
    return "*" in identity.scopes or any(scope == "mcp" or scope.startswith("mcp:") for scope in identity.scopes)


def has_tool_scope(identity: ResolvedIdentity, server: str, tool: str) -> bool:
    accepted = {"*", "mcp", "mcp:*", f"mcp:{server}:*", f"mcp:{server}:{tool}"}
    return any(scope in accepted for scope in identity.scopes)


def validate_argument_constraints(arguments: dict[str, Any], constraints: dict[str, Any]) -> None:
    for path in constraints.get("required_arguments", []):
        if _lookup(arguments, str(path), missing=None) is None:
            raise AuthorizationError(f"Required MCP argument '{path}' is missing")

    for path, allowed in constraints.get("allowed_values", {}).items():
        value = _lookup(arguments, str(path), missing=None)
        if value not in allowed:
            raise AuthorizationError(f"MCP argument '{path}' is outside its allowlist")

    for path, patterns in constraints.get("denied_patterns", {}).items():
        value = _lookup(arguments, str(path), missing="")
        if not isinstance(value, str):
            continue
        for pattern in patterns:
            try:
                matched = re.search(str(pattern), value, re.IGNORECASE)
            except re.error as exc:
                raise AuthorizationError(f"Invalid denied pattern for MCP argument '{path}'") from exc
            if matched:
                raise AuthorizationError(f"MCP argument '{path}' matched a denied pattern")

    max_string_length = int(constraints.get("max_string_length", 0) or 0)
    if max_string_length and any(len(value) > max_string_length for value in _strings(arguments)):
        raise AuthorizationError("MCP arguments exceed the policy string-length limit")


def _matches(rule: dict[str, Any], identity: ResolvedIdentity, server: str, tool: str) -> bool:
    if not fnmatch.fnmatchcase(server, str(rule.get("server", "*"))):
        return False
    if not fnmatch.fnmatchcase(tool, str(rule.get("tool", "*"))):
        return False
    team_ids = rule.get("team_ids")
    if team_ids is not None and identity.team_id not in team_ids:
        return False
    user_ids = rule.get("user_ids")
    return user_ids is None or identity.user_id in user_ids


def _lookup(value: dict[str, Any], path: str, *, missing: Any) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return missing
        current = current[segment]
    return current


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
