import httpx
import pytest
from fastapi import HTTPException

from app.api.auth import _identity_claims, _provider_config
from app.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "oidc__issuer_url": "https://id.example.com",
        "oidc__client_id": "relay",
        "oidc__client_secret": "secret",
        "oidc__allowed_email_domains": ["example.com"],
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_oidc_discovery_requires_matching_issuer_and_endpoints():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://id.example.com/.well-known/openid-configuration"
        return httpx.Response(
            200,
            json={
                "issuer": "https://id.example.com",
                "authorization_endpoint": "https://id.example.com/authorize",
                "token_endpoint": "https://id.example.com/token",
                "userinfo_endpoint": "https://id.example.com/userinfo",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = await _provider_config(_settings(), client)
    assert config["token_endpoint"] == "https://id.example.com/token"


@pytest.mark.asyncio
async def test_oidc_discovery_rejects_issuer_mismatch():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "issuer": "https://attacker.example",
                "authorization_endpoint": "https://attacker.example/authorize",
                "token_endpoint": "https://attacker.example/token",
                "userinfo_endpoint": "https://attacker.example/userinfo",
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException, match="issuer mismatch"):
            await _provider_config(_settings(), client)


def test_oidc_claim_mapping_and_domain_allowlist():
    subject, email, name = _identity_claims(
        _settings(),
        {"sub": "user-1", "email": "Alice@Example.com", "name": "Alice", "email_verified": True},
    )
    assert (subject, email, name) == ("user-1", "Alice@Example.com", "Alice")


def test_oidc_rejects_unverified_or_unapproved_email():
    with pytest.raises(HTTPException, match="verified email"):
        _identity_claims(_settings(), {"sub": "user-1", "email": "alice@example.com"})
    with pytest.raises(HTTPException, match="domain"):
        _identity_claims(
            _settings(),
            {"sub": "user-1", "email": "alice@outside.test", "email_verified": True},
        )
