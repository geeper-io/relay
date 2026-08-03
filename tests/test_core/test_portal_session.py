import pytest

from app.admin.session import issue_admin_session
from app.config import Settings
from app.core.exceptions import AuthenticationError
from app.portal.session import issue_portal_session, verify_portal_session


def test_portal_session_is_signed_and_preserves_identity():
    settings = Settings(proxy_master_key="portal-test-secret", portal__session_ttl_seconds=300)
    token, issued = issue_portal_session(
        settings,
        user_id="user-1",
        email="alice@example.com",
        display_name="Alice",
    )
    resolved = verify_portal_session(token, settings)
    assert resolved == issued
    assert resolved.csrf_token


def test_portal_session_rejects_admin_token_and_tampering():
    settings = Settings(proxy_master_key="portal-test-secret", portal__session_ttl_seconds=300)
    token, _issued = issue_portal_session(
        settings,
        user_id="user-1",
        email="alice@example.com",
        display_name="Alice",
    )
    admin_token, _admin_session = issue_admin_session(settings)
    with pytest.raises(AuthenticationError, match="Invalid"):
        verify_portal_session(admin_token, settings)
    with pytest.raises(AuthenticationError, match="Invalid"):
        verify_portal_session(("A" if token[0] != "A" else "B") + token[1:], settings)


def test_portal_session_expires():
    settings = Settings(proxy_master_key="portal-test-secret", portal__session_ttl_seconds=-1)
    token, _issued = issue_portal_session(
        settings,
        user_id="user-1",
        email="alice@example.com",
        display_name="Alice",
    )
    with pytest.raises(AuthenticationError, match="expired"):
        verify_portal_session(token, settings)
