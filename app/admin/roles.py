"""Durable admin dashboard role assignments."""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AdminIdentity, AdminRoleAssignment, AuditLog

AdminRole = Literal["viewer", "approver", "admin"]
ADMIN_ROLES: frozenset[str] = frozenset({"viewer", "approver", "admin"})


async def record_admin_identity(
    db: AsyncSession,
    *,
    user_id: str,
    email: str,
    display_name: str,
) -> AdminIdentity:
    identity = await db.get(AdminIdentity, user_id)
    if identity is None:
        identity = AdminIdentity(
            user_id=user_id,
            email=email,
            display_name=display_name,
        )
        db.add(identity)
    else:
        identity.email = email
        identity.display_name = display_name
    await db.commit()
    await db.refresh(identity)
    return identity


async def resolve_admin_role(
    db: AsyncSession,
    *,
    user_id: str,
    email: str,
    settings: Settings,
) -> AdminRole | None:
    assignment = await db.scalar(select(AdminRoleAssignment).where(AdminRoleAssignment.user_id == user_id))
    if assignment and assignment.role in ADMIN_ROLES:
        return assignment.role  # type: ignore[return-value]
    bootstrap = {item.strip().lower() for item in settings.admin__bootstrap_emails if item.strip()}
    if email.strip().lower() in bootstrap:
        return "admin"
    return None


async def set_admin_role(
    db: AsyncSession,
    *,
    user_id: str,
    role: AdminRole,
    actor: str,
    request_id: str,
) -> AdminRoleAssignment:
    assignment = await db.scalar(
        select(AdminRoleAssignment).where(AdminRoleAssignment.user_id == user_id).with_for_update()
    )
    action = "admin.role.updated" if assignment else "admin.role.assigned"
    if assignment is None:
        assignment = AdminRoleAssignment(user_id=user_id, role=role, assigned_by=actor)
        db.add(assignment)
    else:
        assignment.role = role
        assignment.assigned_by = actor
    db.add(
        _audit(
            request_id=request_id,
            actor=actor,
            action=action,
            user_id=user_id,
            metadata={"role": role},
        )
    )
    await db.commit()
    await db.refresh(assignment)
    return assignment


async def remove_admin_role(
    db: AsyncSession,
    *,
    user_id: str,
    actor: str,
    request_id: str,
) -> bool:
    assignment = await db.scalar(
        select(AdminRoleAssignment).where(AdminRoleAssignment.user_id == user_id).with_for_update()
    )
    if assignment is None:
        return False
    await db.delete(assignment)
    db.add(
        _audit(
            request_id=request_id,
            actor=actor,
            action="admin.role.removed",
            user_id=user_id,
            metadata={"previous_role": assignment.role},
        )
    )
    await db.commit()
    return True


async def list_admin_roles(
    db: AsyncSession,
    *,
    role: AdminRole | None = None,
    limit: int = 200,
) -> list[AdminRoleAssignment]:
    query = select(AdminRoleAssignment).order_by(AdminRoleAssignment.updated_at.desc()).limit(limit)
    if role:
        query = query.where(AdminRoleAssignment.role == role)
    return list((await db.scalars(query)).all())


async def list_admin_identities(
    db: AsyncSession,
    *,
    limit: int = 200,
) -> list[tuple[AdminIdentity, AdminRoleAssignment | None]]:
    query = (
        select(AdminIdentity, AdminRoleAssignment)
        .outerjoin(AdminRoleAssignment, AdminRoleAssignment.user_id == AdminIdentity.user_id)
        .order_by(AdminIdentity.last_seen_at.desc())
        .limit(limit)
    )
    return list((await db.execute(query)).all())


def _audit(
    *,
    request_id: str,
    actor: str,
    action: str,
    user_id: str,
    metadata: dict,
) -> AuditLog:
    return AuditLog(
        id=str(uuid.uuid4()),
        request_id=request_id,
        user_id=actor,
        action=action,
        resource=f"admin-role/{user_id}",
        metadata_=metadata,
    )
