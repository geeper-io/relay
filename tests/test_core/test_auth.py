from datetime import datetime, timedelta, timezone

import pytest

from app.core.auth import ResolvedIdentity, _is_future, rag_filter_for_identity
from app.core.exceptions import AuthorizationError


def _identity(*scopes: str) -> ResolvedIdentity:
    return ResolvedIdentity(user_id="user-1", team_id="team-1", key_id="key-1", scopes=list(scopes))


def test_scope_matching_supports_exact_and_global_wildcard():
    assert _identity("chat").has_scope("chat")
    assert not _identity("chat").has_scope("embeddings")
    assert _identity("*").has_scope("embeddings")


def test_rag_without_acl_scope_fails_closed():
    assert rag_filter_for_identity(_identity("chat"), None) == {"repo": "__relay_no_access__"}


def test_rag_header_cannot_select_unauthorized_repo():
    with pytest.raises(AuthorizationError):
        rag_filter_for_identity(_identity("chat", "rag:repo:org/allowed"), "org/private")


def test_rag_filter_is_derived_from_all_authorized_repositories():
    result = rag_filter_for_identity(
        _identity("rag:repo:org/backend", "rag:repo:org/frontend"),
        None,
    )
    assert result == {"repo": {"$in": ["org/backend", "org/frontend"]}}


def test_rag_global_scope_can_query_all_or_narrow_explicitly():
    identity = _identity("rag:*")
    assert rag_filter_for_identity(identity, None) is None
    assert rag_filter_for_identity(identity, "org/backend") == {"repo": "org/backend"}


def test_expiry_helper_accepts_aware_and_naive_datetimes():
    assert _is_future(datetime.now(timezone.utc) + timedelta(minutes=1))
    assert _is_future(datetime.now() + timedelta(minutes=1))
    assert not _is_future(datetime.now(timezone.utc) - timedelta(minutes=1))
