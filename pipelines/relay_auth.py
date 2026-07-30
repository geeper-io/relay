"""
title: Relay Auth Pipeline
author: geeper-io
version: 1.0.0
description: >
  Automatically provisions a per-user Relay API key on first request and
  proxies all subsequent requests to Relay using that key.
  Requires RELAY_MASTER_KEY and RELAY_BASE_URL to be set in Valves.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional, Union

import httpx
from pydantic import BaseModel


class Pipeline:
    class Valves(BaseModel):
        # Internal service URL — use cluster-local DNS when running in Kubernetes.
        # Never expose Relay's admin endpoints to the public internet.
        RELAY_BASE_URL: str = "http://relay.relay.svc.cluster.local:8000"

        # Relay master key — store as a K8s secret, not hardcoded.
        RELAY_MASTER_KEY: str = ""

        # Optional: assign all auto-provisioned users to this team ID.
        DEFAULT_TEAM_ID: str = ""

        # Shared secret between Open WebUI and this pipeline server.
        # Set PIPELINES_API_KEY in Open WebUI to the same value.
        PIPELINES_API_KEY: str = ""

    def __init__(self):
        self.name = "Relay"
        self.valves = self.Valves()
        # In-memory cache: email -> gr-... key.
        # Lost on pipeline restart — a new key is provisioned automatically.
        # Old keys remain valid; they are not rotated.
        self._key_cache: dict[str, str] = {}

    async def on_startup(self) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def pipes(self) -> list[dict]:
        """Fetch available models from Relay and expose them to Open WebUI."""
        try:
            resp = httpx.get(
                f"{self.valves.RELAY_BASE_URL}/v1/models",
                headers={"Authorization": f"Bearer {self.valves.RELAY_MASTER_KEY}"},
                timeout=5,
            )
            resp.raise_for_status()
            return [{"id": m["id"], "name": m["id"]} for m in resp.json().get("data", [])]
        except Exception:
            return [{"id": "relay-unavailable", "name": "Relay (unavailable)"}]

    def _admin_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.valves.RELAY_MASTER_KEY}"}

    async def _get_or_create_key(self, email: str) -> str:
        """Return the cached Relay API key for *email*, creating one if needed."""
        if email in self._key_cache:
            return self._key_cache[email]

        async with httpx.AsyncClient(timeout=10) as client:
            # 1. Look up existing user
            resp = await client.get(
                f"{self.valves.RELAY_BASE_URL}/internal/users",
                params={"external_id": email},
                headers=self._admin_headers(),
            )

            if resp.status_code == 200:
                user_id = resp.json()["id"]
            else:
                # 2. Create user
                params: dict = {"external_id": email}
                if self.valves.DEFAULT_TEAM_ID:
                    params["team_id"] = self.valves.DEFAULT_TEAM_ID

                resp = await client.post(
                    f"{self.valves.RELAY_BASE_URL}/internal/users",
                    params=params,
                    headers=self._admin_headers(),
                )
                resp.raise_for_status()
                user_id = resp.json()["id"]

            # 3. Create API key
            resp = await client.post(
                f"{self.valves.RELAY_BASE_URL}/internal/api-keys",
                params={"user_id": user_id, "name": "openwebui"},
                headers=self._admin_headers(),
            )
            resp.raise_for_status()
            key: str = resp.json()["key"]

        self._key_cache[email] = key
        return key

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
    ) -> Union[str, AsyncIterator]:
        email = (__user__ or {}).get("email", "anonymous")

        relay_key = await self._get_or_create_key(email)

        # Open WebUI prefixes model ids with the pipeline id ("relay.modelname").
        # Strip it before forwarding so Relay sees the bare model name.
        model: str = body.get("model", "")
        if "." in model:
            model = model.split(".", 1)[1]

        headers = {"Authorization": f"Bearer {relay_key}"}
        payload = {**body, "model": model}

        if body.get("stream"):
            return self._stream(headers, payload)

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.valves.RELAY_BASE_URL}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def _stream(self, headers: dict, payload: dict) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.valves.RELAY_BASE_URL}/v1/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        yield line + "\n"
