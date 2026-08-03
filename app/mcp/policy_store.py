"""Database-backed MCP policy versions, activation, validation, and simulation support."""

from __future__ import annotations

import difflib
import fnmatch
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.exceptions import AuthorizationError
from app.db.models import AuditLog, MCPPolicyActivation, MCPPolicyState, MCPPolicyVersion
from app.mcp.policy import MCPPolicyEngine

_ACTIONS = {"allow", "require_approval", "deny"}
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_POLICY_KEYS = {"default_action", "rules"}
_RULE_KEYS = {
    "name",
    "server",
    "tool",
    "action",
    "reason",
    "required_scopes",
    "team_ids",
    "user_ids",
    "constraints",
    "grant",
}
_CONSTRAINT_KEYS = {"required_arguments", "allowed_values", "denied_patterns", "max_string_length"}
_GRANT_KEYS = {"subject", "ttl_seconds", "max_calls", "constraints", "tool_pattern", "workflow_id", "reason"}


@dataclass(frozen=True)
class MCPPolicySnapshot:
    version: str
    document: dict[str, Any]
    source: str


@dataclass(frozen=True)
class MCPPolicyValidation:
    valid: bool
    errors: list[str]
    warnings: list[str]
    rule_count: int
    actions: dict[str, int]


def configured_policy_snapshot(settings: Settings) -> MCPPolicySnapshot:
    version = settings.mcp_active_policy_version
    return MCPPolicySnapshot(version, dict(settings.mcp_policies.get(version, {})), "configuration")


async def load_active_policy(db: AsyncSession, settings: Settings) -> MCPPolicySnapshot:
    state = await db.get(MCPPolicyState, "mcp")
    if state is None:
        return configured_policy_snapshot(settings)
    policy = await db.get(MCPPolicyVersion, state.active_version)
    if policy is None:
        raise AuthorizationError("Active MCP policy state is inconsistent")
    return MCPPolicySnapshot(policy.version, dict(policy.document or {}), "database")


async def load_policy_version(
    db: AsyncSession,
    settings: Settings,
    version: str,
) -> MCPPolicySnapshot | None:
    policy = await db.get(MCPPolicyVersion, version)
    if policy is not None:
        return MCPPolicySnapshot(policy.version, dict(policy.document or {}), "database")
    configured = settings.mcp_policies.get(version)
    if configured is not None:
        return MCPPolicySnapshot(version, dict(configured), "configuration")
    return None


async def active_policy_engine(db: AsyncSession, settings: Settings) -> tuple[MCPPolicyEngine, MCPPolicySnapshot]:
    snapshot = await load_active_policy(db, settings)
    return (
        MCPPolicyEngine(
            settings,
            policy_version=snapshot.version,
            policy_document=snapshot.document,
        ),
        snapshot,
    )


def validate_policy_document(document: Any, settings: Settings) -> MCPPolicyValidation:
    errors: list[str] = []
    warnings: list[str] = []
    actions = {action: 0 for action in sorted(_ACTIONS)}
    if not isinstance(document, dict):
        return MCPPolicyValidation(False, ["Policy document must be a JSON object"], [], 0, actions)
    unknown_policy_keys = set(document) - _POLICY_KEYS
    if unknown_policy_keys:
        errors.append("Unknown policy fields: " + ", ".join(sorted(unknown_policy_keys)))
    default_action = document.get("default_action", "deny")
    if default_action not in _ACTIONS:
        errors.append("default_action must be allow, require_approval, or deny")
    if default_action == "allow":
        warnings.append("A default allow policy exposes every scoped tool not matched by an earlier rule")
    rules = document.get("rules", [])
    if not isinstance(rules, list):
        return MCPPolicyValidation(False, [*errors, "rules must be an array"], warnings, 0, actions)
    if len(rules) > 500:
        errors.append("Policy cannot contain more than 500 rules")
    seen_names: set[str] = set()
    configured_servers = set(settings.mcp_servers)
    for index, rule in enumerate(rules):
        prefix = f"rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{prefix} must be an object")
            continue
        unknown = set(rule) - _RULE_KEYS
        if unknown:
            errors.append(f"{prefix} has unknown fields: " + ", ".join(sorted(unknown)))
        name = rule.get("name")
        if name is not None:
            if not isinstance(name, str) or not name or len(name) > 100:
                errors.append(f"{prefix}.name must be a non-empty string up to 100 characters")
            elif name in seen_names:
                errors.append(f"{prefix}.name duplicates '{name}'")
            else:
                seen_names.add(name)
        action = rule.get("action", "deny")
        if action not in _ACTIONS:
            errors.append(f"{prefix}.action must be allow, require_approval, or deny")
        else:
            actions[action] += 1
        server_pattern = rule.get("server", "*")
        tool_pattern = rule.get("tool", "*")
        if not isinstance(server_pattern, str) or not server_pattern:
            errors.append(f"{prefix}.server must be a non-empty glob string")
        elif configured_servers and not any(
            fnmatch.fnmatchcase(server, server_pattern) for server in configured_servers
        ):
            warnings.append(f"{prefix}.server matches no configured MCP server")
        if not isinstance(tool_pattern, str) or not tool_pattern:
            errors.append(f"{prefix}.tool must be a non-empty glob string")
        for field in ("required_scopes", "team_ids", "user_ids"):
            value = rule.get(field)
            if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
                errors.append(f"{prefix}.{field} must be an array of strings")
        _validate_constraints(rule.get("constraints", {}), f"{prefix}.constraints", errors)
        grant = rule.get("grant")
        if grant is not None:
            if action != "require_approval":
                errors.append(f"{prefix}.grant is only valid for require_approval rules")
            _validate_grant(grant, f"{prefix}.grant", errors)
    if not any(rule.get("action") == "deny" for rule in rules if isinstance(rule, dict)) and default_action != "deny":
        warnings.append("Policy has no explicit or default deny path")
    return MCPPolicyValidation(not errors, errors, warnings, len(rules), actions)


def _validate_constraints(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    unknown = set(value) - _CONSTRAINT_KEYS
    if unknown:
        errors.append(f"{path} has unknown fields: " + ", ".join(sorted(unknown)))
    required = value.get("required_arguments", [])
    if not isinstance(required, list) or not all(isinstance(item, str) and item for item in required):
        errors.append(f"{path}.required_arguments must be an array of non-empty strings")
    allowed = value.get("allowed_values", {})
    if not isinstance(allowed, dict) or not all(isinstance(values, list) for values in allowed.values()):
        errors.append(f"{path}.allowed_values must map argument paths to arrays")
    denied = value.get("denied_patterns", {})
    if not isinstance(denied, dict) or not all(isinstance(patterns, list) for patterns in denied.values()):
        errors.append(f"{path}.denied_patterns must map argument paths to regex arrays")
    elif isinstance(denied, dict):
        for argument, patterns in denied.items():
            for pattern in patterns:
                try:
                    re.compile(str(pattern))
                except re.error:
                    errors.append(f"{path}.denied_patterns.{argument} contains an invalid regular expression")
    maximum = value.get("max_string_length", 0)
    if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0):
        errors.append(f"{path}.max_string_length must be a non-negative integer")


def _validate_grant(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    unknown = set(value) - _GRANT_KEYS
    if unknown:
        errors.append(f"{path} has unknown fields: " + ", ".join(sorted(unknown)))
    if value.get("subject", "user") not in {"user", "team"}:
        errors.append(f"{path}.subject must be user or team")
    ttl = value.get("ttl_seconds", 3600)
    if not isinstance(ttl, int) or isinstance(ttl, bool) or not 60 <= ttl <= 2_592_000:
        errors.append(f"{path}.ttl_seconds must be between 60 and 2592000")
    calls = value.get("max_calls", 1)
    if not isinstance(calls, int) or isinstance(calls, bool) or not 1 <= calls <= 10_000:
        errors.append(f"{path}.max_calls must be between 1 and 10000")
    _validate_constraints(value.get("constraints", {}), f"{path}.constraints", errors)


async def create_policy_draft(
    db: AsyncSession,
    *,
    version: str,
    document: dict[str, Any],
    base_version: str | None,
    actor: str,
    reason: str | None,
    request_id: str,
    settings: Settings,
) -> MCPPolicyVersion:
    if not _VERSION_RE.fullmatch(version):
        raise AuthorizationError("Policy version must use letters, numbers, dots, dashes, or underscores")
    validation = validate_policy_document(document, settings)
    if not validation.valid:
        raise AuthorizationError("Invalid MCP policy: " + "; ".join(validation.errors))
    if await db.get(MCPPolicyVersion, version) is not None or version in settings.mcp_policies:
        raise AuthorizationError(f"MCP policy version '{version}' already exists")
    if base_version and await load_policy_version(db, settings, base_version) is None:
        raise AuthorizationError(f"Base MCP policy version '{base_version}' does not exist")
    policy = MCPPolicyVersion(
        version=version,
        document=document,
        status="draft",
        base_version=base_version,
        created_by=actor,
        reason=reason,
    )
    db.add(policy)
    db.add(_audit(request_id, actor, "mcp.policy.created", version, {"base_version": base_version, "reason": reason}))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthorizationError(f"MCP policy version '{version}' already exists") from exc
    await db.refresh(policy)
    return policy


async def activate_policy_version(
    db: AsyncSession,
    *,
    version: str,
    actor: str,
    reason: str | None,
    request_id: str,
    settings: Settings,
) -> MCPPolicyVersion:
    state = await db.scalar(select(MCPPolicyState).where(MCPPolicyState.id == "mcp").with_for_update())
    target = await db.scalar(select(MCPPolicyVersion).where(MCPPolicyVersion.version == version).with_for_update())
    if target is None:
        configured = settings.mcp_policies.get(version)
        if configured is None:
            raise AuthorizationError(f"MCP policy version '{version}' does not exist")
        target = MCPPolicyVersion(
            version=version,
            document=dict(configured),
            status="draft",
            base_version=None,
            created_by="configuration",
            reason="Imported from Relay configuration",
        )
        db.add(target)
        await db.flush()
    validation = validate_policy_document(target.document, settings)
    if not validation.valid:
        raise AuthorizationError("Invalid MCP policy: " + "; ".join(validation.errors))
    previous_version = state.active_version if state else settings.mcp_active_policy_version
    if state and state.active_version == version:
        raise AuthorizationError(f"MCP policy version '{version}' is already active")
    if state:
        previous = await db.get(MCPPolicyVersion, state.active_version)
        if previous:
            previous.status = "archived"
        state.active_version = version
        state.updated_by = actor
        state.updated_at = datetime.now(timezone.utc)
    else:
        state = MCPPolicyState(id="mcp", active_version=version, updated_by=actor)
        db.add(state)
    target.status = "active"
    target.activated_by = actor
    target.activated_at = datetime.now(timezone.utc)
    db.add(
        MCPPolicyActivation(
            id=str(uuid.uuid4()),
            version=version,
            previous_version=previous_version,
            actor=actor,
            reason=reason,
        )
    )
    db.add(
        _audit(
            request_id,
            actor,
            "mcp.policy.activated",
            version,
            {"previous_version": previous_version, "reason": reason},
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthorizationError("MCP policy activation conflicted with another control-plane update; retry") from exc
    await db.refresh(target)
    return target


async def list_policy_versions(db: AsyncSession) -> list[MCPPolicyVersion]:
    return list((await db.scalars(select(MCPPolicyVersion).order_by(MCPPolicyVersion.created_at.desc()))).all())


async def list_policy_activations(db: AsyncSession, *, limit: int = 50) -> list[MCPPolicyActivation]:
    return list(
        (
            await db.scalars(select(MCPPolicyActivation).order_by(MCPPolicyActivation.created_at.desc()).limit(limit))
        ).all()
    )


def policy_diff(base: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    before = json.dumps(base, indent=2, sort_keys=True).splitlines()
    after = json.dumps(candidate, indent=2, sort_keys=True).splitlines()
    return list(difflib.unified_diff(before, after, fromfile="active", tofile="candidate", lineterm=""))


def _audit(
    request_id: str,
    actor: str,
    action: str,
    version: str,
    metadata: dict[str, Any],
) -> AuditLog:
    return AuditLog(
        id=str(uuid.uuid4()),
        request_id=request_id,
        user_id=actor,
        action=action,
        resource=f"mcp-policy/{version}",
        metadata_={"version": version, **metadata},
    )
