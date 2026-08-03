#!/usr/bin/env python3
"""Seed and run an isolated local Relay admin-dashboard demo."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEMO_MASTER_KEY = "relay-demo-master-key-2026"


def _configure(database_path: Path, master_key: str, host: str, port: int) -> None:
    values = {
        "CONFIG_FILE": os.devnull,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
        "PROXY_MASTER_KEY": master_key,
        "OPENAI_API_KEY": "sk-demo-not-used",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "ADMIN__ENABLED": "true",
        "ADMIN__SECURE_COOKIES": "false",
        "ADMIN__ALLOW_MASTER_KEY_LOGIN": "true",
        "ADMIN__OIDC_ENABLED": "false",
        "RAG__ENABLED": "false",
        "PII__ENABLED": "false",
        "RATE_LIMITING__ENABLED": "false",
        "CONTENT_POLICY__ENABLED": "false",
        "ANALYTICS__ENABLED": "false",
        "TELEMETRY__ENABLED": "false",
        "SERVER__WORKERS": "1",
        "SERVER__LOG_LEVEL": "warning",
        "SERVER__HOST": host,
        "SERVER__PORT": str(port),
        "MCP__ENABLED": "true",
        "MCP__PUBLIC_URL": f"http://{host}:{port}/mcp",
        "MCP__ALLOW_INSECURE_HTTP": "true",
        "MCP__ACTIVE_POLICY_VERSION": "2026-07-demo",
        "MCP__SERVERS": json.dumps(
            {
                "code": {
                    "url": "http://127.0.0.1:65535/mcp",
                    "description": "Demo code runner (inventory only)",
                }
            }
        ),
        "MCP__POLICIES": json.dumps(
            {
                "2026-07-demo": {
                    "default_action": "require_approval",
                    "rules": [
                        {
                            "server": "code",
                            "tool": "execute",
                            "action": "require_approval",
                            "grant": {"subject": "user", "ttl_seconds": 28800, "max_calls": 20},
                        }
                    ],
                }
            }
        ),
    }
    os.environ.update(values)


def _identifier() -> str:
    return str(uuid.uuid4())


async def seed_demo(seed: int) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.db.engine import create_all_tables, get_session_factory
    from app.db.models import (
        AdminIdentity,
        AdminRoleAssignment,
        ApiKey,
        AuditLog,
        MCPApproval,
        MCPApprovalGrant,
        MCPApprovalGrantOffer,
        MCPPolicyActivation,
        MCPPolicyState,
        MCPPolicyVersion,
        Team,
        UsageRecord,
        User,
    )
    from app.mcp.approvals import arguments_hash

    await create_all_tables()
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    factory = get_session_factory()

    team_specs = [
        ("Platform", 1_200_000, 12_000_000),
        ("Product", 850_000, 8_000_000),
        ("Data & AI", 1_500_000, 16_000_000),
        ("Customer Operations", 600_000, 6_000_000),
    ]
    people = [
        ("Avery", "Chen"),
        ("Maya", "Patel"),
        ("Noah", "Williams"),
        ("Sofia", "Martinez"),
        ("Leo", "Andersson"),
        ("Amara", "Okafor"),
        ("Elias", "Niemi"),
        ("Nora", "Kowalski"),
        ("Theo", "Dubois"),
        ("Layla", "Haddad"),
        ("Oliver", "Smith"),
        ("Emma", "Johansson"),
        ("Mateo", "Garcia"),
        ("Freya", "Wilson"),
        ("Arjun", "Mehta"),
        ("Mila", "Petrova"),
        ("Liam", "O'Connor"),
        ("Zara", "Khan"),
        ("Daniel", "Kim"),
        ("Iris", "Wang"),
        ("Jonas", "Berg"),
        ("Elena", "Rossi"),
        ("Sam", "Taylor"),
        ("Ines", "Costa"),
    ]
    models = {
        "gpt-4o": 0.000009,
        "gpt-4o-mini": 0.0000012,
        "claude-3-5-sonnet-20241022": 0.000008,
        "azure/gpt-4o": 0.0000095,
    }

    async with factory() as db:
        if await db.scalar(select(func.count(User.id))):
            raise RuntimeError("Demo database is not empty; choose a new --database path")

        teams = [
            Team(id=_identifier(), name=name, tpm_limit=tpm, daily_token_limit=daily) for name, tpm, daily in team_specs
        ]
        db.add_all(teams)

        users: list[User] = []
        for index, (first, last) in enumerate(people):
            custom_limits = index % 5 == 0
            user = User(
                id=_identifier(),
                external_id=f"{first}.{last}".replace("'", "").lower() + "@acme.example",
                team_id=teams[index % len(teams)].id,
                rpm_limit=rng.choice([90, 120, 180]) if custom_limits else None,
                tpm_limit=rng.choice([150_000, 250_000, 400_000]) if custom_limits else None,
                is_active=index not in {17, 22},
                created_at=now - timedelta(days=rng.randint(45, 500)),
            )
            users.append(user)
        db.add_all(users)

        api_keys: list[ApiKey] = []
        for user_index, user in enumerate(users):
            for key_index in range(rng.randint(1, 3)):
                created_at = now - timedelta(days=rng.randint(4, 240))
                is_active = not (key_index == 2 or user_index in {17, 22})
                key_id = _identifier()
                api_keys.append(
                    ApiKey(
                        id=key_id,
                        key_hash=hashlib.sha256(f"demo:{seed}:{key_id}".encode()).hexdigest(),
                        key_prefix=f"gr-demo{user_index:02d}{key_index}",
                        user_id=user.id,
                        name=rng.choice(["workstation", "ci-runner", "notebook", "service"]),
                        scopes=rng.choice(
                            [
                                ["chat", "responses"],
                                ["chat", "embeddings"],
                                ["chat", "responses", "mcp"],
                            ]
                        ),
                        is_active=is_active,
                        created_at=created_at,
                        last_used_at=(
                            now - timedelta(hours=rng.randint(1, 500)) if is_active and rng.random() > 0.12 else None
                        ),
                        expires_at=now + timedelta(days=rng.randint(20, 180)) if is_active else None,
                    )
                )
        db.add_all(api_keys)

        usage: list[UsageRecord] = []
        audit: list[AuditLog] = []
        for day_offset in range(45):
            weekday_factor = 0.55 if (now - timedelta(days=day_offset)).weekday() >= 5 else 1.0
            daily_requests = int(rng.randint(18, 42) * weekday_factor)
            for _ in range(daily_requests):
                user = rng.choices(users, weights=[8, 7, 7, 6, 6, 5, 5, 5] + [3] * 16, k=1)[0]
                model = rng.choices(list(models), weights=[40, 34, 18, 8], k=1)[0]
                prompt_tokens = rng.randint(180, 6_500)
                completion_tokens = rng.randint(60, 2_200)
                total_tokens = prompt_tokens + completion_tokens
                status = "error" if rng.random() < 0.035 else "success"
                created_at = now - timedelta(
                    days=day_offset,
                    hours=rng.randint(0, 22),
                    minutes=rng.randint(0, 59),
                )
                request_id = _identifier()
                record = UsageRecord(
                    id=_identifier(),
                    user_id=user.id,
                    team_id=user.team_id,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=rng.randint(180, 3_200),
                    request_id=request_id,
                    cost_usd=round(total_tokens * models[model], 6),
                    cache_hit=rng.random() < 0.21,
                    was_rag_used=rng.random() < 0.28,
                    pii_entities_scrubbed=rng.choices([0, 1, 2, 3], weights=[75, 17, 6, 2], k=1)[0],
                    status=status,
                    error_code=rng.choice(["upstream_timeout", "rate_limited", "provider_error"])
                    if status == "error"
                    else None,
                    created_at=created_at,
                )
                usage.append(record)
                if rng.random() < 0.08:
                    audit.append(
                        AuditLog(
                            id=_identifier(),
                            request_id=request_id,
                            user_id=user.id,
                            action="llm.request",
                            resource=model,
                            metadata_={"team_id": user.team_id, "status": status, "demo": True},
                            created_at=created_at,
                        )
                    )
        db.add_all(usage)
        db.add_all(audit)

        approval_specs = [
            ("github", "create_pull_request", {"repository": "acme/payments", "branch": "fix/retry-budget"}),
            ("code", "execute", {"runtime": "python", "command": "pytest tests/payments -q"}),
            ("snowflake", "run_query", {"warehouse": "ANALYTICS", "query": "SELECT region, count(*) FROM orders"}),
            ("slack", "post_message", {"channel": "#incident-response", "text": "Mitigation is ready"}),
            ("jira", "create_issue", {"project": "PLAT", "summary": "Increase gateway retry budget"}),
            ("github", "merge_pull_request", {"repository": "acme/relay", "pull_number": 418}),
            ("code", "execute", {"runtime": "node", "command": "npm run integration"}),
            ("snowflake", "run_query", {"warehouse": "FINANCE", "query": "SELECT sum(cost) FROM usage_daily"}),
            ("slack", "post_message", {"channel": "#product-ai", "text": "Evaluation run completed"}),
            ("github", "create_release", {"repository": "acme/relay", "tag": "v1.8.0"}),
        ]
        approvals: list[MCPApproval] = []
        for index, (server, tool, arguments) in enumerate(approval_specs):
            status = "pending" if index < 5 else rng.choice(["approved", "denied", "consumed"])
            requested_at = now - timedelta(minutes=rng.randint(2, 800))
            decided_at = None if status == "pending" else requested_at + timedelta(minutes=rng.randint(2, 25))
            approvals.append(
                MCPApproval(
                    id=_identifier(),
                    user_id=users[index].id,
                    team_id=users[index].team_id,
                    server_name=server,
                    tool_name=tool,
                    arguments_hash=arguments_hash(arguments),
                    arguments=arguments,
                    purpose=rng.choice(
                        [
                            "Validate the release before production deployment",
                            "Investigate the customer-impacting incident",
                            "Prepare the weekly operating review",
                            "Automate a reviewed engineering workflow",
                        ]
                    ),
                    policy_version="2026-07-demo",
                    status=status,
                    requested_at=requested_at,
                    expires_at=now + timedelta(minutes=rng.randint(20, 180)),
                    decided_at=decided_at,
                    decided_by="oidc:demo-admin" if decided_at else None,
                    decision_reason="Reviewed against the demo policy" if decided_at else None,
                    consumed_at=decided_at + timedelta(minutes=2) if status == "consumed" else None,
                )
            )
        db.add_all(approvals)
        db.add_all(
            [
                MCPApprovalGrantOffer(
                    approval_id=approvals[0].id,
                    template={"subject": "user", "ttl_seconds": 28800, "max_calls": 12},
                ),
                MCPApprovalGrantOffer(
                    approval_id=approvals[1].id,
                    template={
                        "subject": "team",
                        "ttl_seconds": 3600,
                        "max_calls": 20,
                        "constraints": {"allowed_values": {"runtime": ["python"]}},
                        "workflow_id": "release-validation",
                    },
                ),
            ]
        )

        grants = [
            MCPApprovalGrant(
                id=_identifier(),
                subject_type="user",
                subject_id=users[0].id,
                server_name="code",
                tool_pattern="execute",
                constraints={"allowed_values": {"runtime": ["python"]}},
                policy_version="2026-07-demo",
                max_calls=20,
                calls_used=7,
                expires_at=now + timedelta(hours=6),
                created_by="demo-admin",
                reason="Release validation commands in the Python sandbox",
                workflow_id="release-validation",
                created_at=now - timedelta(hours=2),
            ),
            MCPApprovalGrant(
                id=_identifier(),
                subject_type="team",
                subject_id=teams[0].id,
                server_name="code",
                tool_pattern="test_*",
                constraints={},
                policy_version="2026-07-demo",
                max_calls=100,
                calls_used=34,
                expires_at=now + timedelta(days=3),
                created_by="demo-admin",
                reason="Routine Platform team test jobs",
                created_at=now - timedelta(days=1),
            ),
            MCPApprovalGrant(
                id=_identifier(),
                subject_type="user",
                subject_id=users[4].id,
                server_name="code",
                tool_pattern="execute",
                constraints={},
                policy_version="2026-07-demo",
                max_calls=5,
                calls_used=5,
                expires_at=now + timedelta(hours=2),
                created_by="demo-admin",
                reason="One-off dependency audit",
                created_at=now - timedelta(hours=5),
            ),
            MCPApprovalGrant(
                id=_identifier(),
                subject_type="user",
                subject_id=users[6].id,
                server_name="code",
                tool_pattern="execute",
                constraints={},
                policy_version="2026-07-demo",
                max_calls=10,
                calls_used=2,
                expires_at=now - timedelta(hours=1),
                created_by="demo-admin",
                reason="Expired incident investigation",
                created_at=now - timedelta(days=2),
            ),
            MCPApprovalGrant(
                id=_identifier(),
                subject_type="team",
                subject_id=teams[2].id,
                server_name="code",
                tool_pattern="notebook_*",
                constraints={},
                policy_version="2026-07-demo",
                max_calls=50,
                calls_used=11,
                expires_at=now + timedelta(days=5),
                revoked_at=now - timedelta(minutes=40),
                created_by="demo-admin",
                reason="Data migration completed early",
                created_at=now - timedelta(days=1),
            ),
        ]
        db.add_all(grants)

        baseline_policy = {
            "default_action": "deny",
            "rules": [
                {"name": "safe-tests", "server": "code", "tool": "test_*", "action": "allow"},
                {
                    "name": "reviewed-execution",
                    "server": "code",
                    "tool": "execute",
                    "action": "require_approval",
                    "constraints": {"allowed_values": {"runtime": ["python", "node"]}},
                },
            ],
        }
        active_policy = {
            "default_action": "deny",
            "rules": [
                {"name": "safe-tests", "server": "code", "tool": "test_*", "action": "allow"},
                {
                    "name": "reviewed-execution",
                    "server": "code",
                    "tool": "execute",
                    "action": "require_approval",
                    "constraints": {
                        "allowed_values": {"runtime": ["python", "node"]},
                        "denied_patterns": {"command": ["sudo", "rm\\s+-rf"]},
                    },
                    "grant": {"subject": "user", "ttl_seconds": 28800, "max_calls": 20},
                },
            ],
        }
        candidate_policy = {
            "default_action": "deny",
            "rules": [
                {"name": "safe-tests", "server": "code", "tool": "test_*", "action": "allow"},
                {
                    "name": "python-only-execution",
                    "server": "code",
                    "tool": "execute",
                    "action": "require_approval",
                    "constraints": {"allowed_values": {"runtime": ["python"]}},
                },
            ],
        }
        policy_versions = [
            MCPPolicyVersion(
                version="2026-06-demo",
                document=baseline_policy,
                status="archived",
                created_by="demo-admin",
                reason="Initial deny-by-default MCP policy",
                created_at=now - timedelta(days=28),
                activated_by="demo-admin",
                activated_at=now - timedelta(days=27),
            ),
            MCPPolicyVersion(
                version="2026-07-demo",
                document=active_policy,
                status="active",
                base_version="2026-06-demo",
                created_by="demo-admin",
                reason="Add bounded grants and destructive command guards",
                created_at=now - timedelta(days=8),
                activated_by="demo-admin",
                activated_at=now - timedelta(days=7),
            ),
            MCPPolicyVersion(
                version="2026-08-candidate",
                document=candidate_policy,
                status="draft",
                base_version="2026-07-demo",
                created_by="demo-admin",
                reason="Evaluate Python-only execution for regulated teams",
                created_at=now - timedelta(hours=18),
            ),
        ]
        db.add_all(policy_versions)
        db.add(MCPPolicyState(id="mcp", active_version="2026-07-demo", updated_by="demo-admin"))
        db.add_all(
            [
                MCPPolicyActivation(
                    id=_identifier(),
                    version="2026-06-demo",
                    previous_version=None,
                    actor="demo-admin",
                    reason="Initial policy rollout",
                    created_at=now - timedelta(days=27),
                ),
                MCPPolicyActivation(
                    id=_identifier(),
                    version="2026-07-demo",
                    previous_version="2026-06-demo",
                    actor="demo-admin",
                    reason="Policy evaluation suite passed",
                    created_at=now - timedelta(days=7),
                ),
            ]
        )

        admin_roles = ["admin", "approver", "viewer"]
        for user, role in zip(users[:3], admin_roles, strict=True):
            first_name = user.external_id.split(".", 1)[0].title()
            db.add(
                AdminIdentity(
                    user_id=user.id,
                    email=user.external_id,
                    display_name=f"{first_name} Demo",
                    last_seen_at=now - timedelta(hours=rng.randint(1, 36)),
                )
            )
            db.add(
                AdminRoleAssignment(
                    user_id=user.id,
                    role=role,
                    assigned_by="demo-seed",
                )
            )

        await db.commit()
        return {
            "teams": len(teams),
            "users": len(users),
            "api_keys": len(api_keys),
            "usage_records": len(usage),
            "approvals": len(approvals),
            "approval_grants": len(grants),
            "policy_versions": len(policy_versions),
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Relay's admin dashboard with isolated synthetic demo data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible demo data")
    parser.add_argument("--master-key", default=DEMO_MASTER_KEY, help="Local demo login key")
    parser.add_argument("--database", type=Path, help="Optional persistent SQLite path; must be empty")
    parser.add_argument("--seed-only", action="store_true", help="Seed the database without starting Relay")
    return parser


def _run(args: argparse.Namespace, database_path: Path) -> None:
    _configure(database_path.resolve(), args.master_key, args.host, args.port)
    counts = asyncio.run(seed_demo(args.seed))
    print("\nRelay admin demo is ready")
    print("  Data:  " + ", ".join(f"{value} {name.replace('_', ' ')}" for name, value in counts.items()))
    print(f"  DB:    {database_path}")
    if args.seed_only:
        return
    print(f"  URL:   http://{args.host}:{args.port}/admin")
    print(f"  Login: {args.master_key}")
    print("  Stop:  Ctrl-C (temporary demo data will be removed)\n")

    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")


def main() -> None:
    args = _parser().parse_args()
    if args.database:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        _run(args, args.database)
        return
    with tempfile.TemporaryDirectory(prefix="relay-admin-demo-") as directory:
        _run(args, Path(directory) / "relay-demo.db")


if __name__ == "__main__":
    main()
