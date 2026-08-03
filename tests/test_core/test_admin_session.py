import pytest

from app.admin.session import issue_admin_session, verify_admin_session
from app.config import Settings
from app.core.exceptions import AuthenticationError


def test_admin_session_is_signed_and_contains_csrf_token():
    settings = Settings(proxy_master_key="test-master-key", admin__session_ttl_seconds=300)
    token, issued = issue_admin_session(settings)
    resolved = verify_admin_session(token, settings)
    assert resolved.csrf_token == issued.csrf_token
    assert resolved.expires_at > resolved.issued_at
    assert resolved.role == "admin"
    assert resolved.actor == "master-key"


def test_admin_session_preserves_oidc_identity_and_role():
    settings = Settings(proxy_master_key="test-master-key", admin__session_ttl_seconds=300)
    token, _issued = issue_admin_session(
        settings,
        role="approver",
        actor="oidc:user-1",
        user_id="user-1",
        email="alice@example.com",
        display_name="Alice",
    )
    resolved = verify_admin_session(token, settings)
    assert resolved.role == "approver"
    assert resolved.actor == "oidc:user-1"
    assert resolved.user_id == "user-1"
    assert resolved.email == "alice@example.com"
    assert resolved.display_name == "Alice"


def test_admin_session_rejects_tampering_and_expiry():
    settings = Settings(proxy_master_key="test-master-key", admin__session_ttl_seconds=300)
    token, _issued = issue_admin_session(settings)
    with pytest.raises(AuthenticationError, match="Invalid"):
        verify_admin_session(token[:-1] + ("A" if token[-1] != "A" else "B"), settings)

    expired_settings = Settings(proxy_master_key="test-master-key", admin__session_ttl_seconds=-1)
    expired, _issued = issue_admin_session(expired_settings)
    with pytest.raises(AuthenticationError, match="expired"):
        verify_admin_session(expired, expired_settings)
