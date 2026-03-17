#!/usr/bin/env python3
"""CLI script to provision a user + API key."""

import asyncio
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
from app.db.engine import create_all_tables, get_session_factory
from app.db.repositories.users import (
    create_api_key,
    create_team,
    create_user,
    get_user_by_external_id,
)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Create a user + API key for the LLM proxy")
    parser.add_argument("--external-id", required=True, help="User's external/SSO identifier")
    parser.add_argument("--team", default=None, help="Team name (will be created if it doesn't exist)")
    parser.add_argument("--key-name", default="default", help="Name for the API key")
    args = parser.parse_args()

    get_settings()  # loads config and seeds environment
    await create_all_tables()

    factory = get_session_factory()
    async with factory() as db:
        # Resolve or create team
        team_id = None
        if args.team:
            from sqlalchemy import select

            from app.db.models import Team

            result = await db.execute(select(Team).where(Team.name == args.team))
            team = result.scalar_one_or_none()
            if not team:
                team = await create_team(db, name=args.team)
                print(f"Created team: {team.name} ({team.id})")
            else:
                print(f"Using existing team: {team.name} ({team.id})")
            team_id = team.id

        # Resolve or create user
        user = await get_user_by_external_id(db, args.external_id)
        if not user:
            user = await create_user(db, external_id=args.external_id, team_id=team_id)
            print(f"Created user: {user.external_id} ({user.id})")
        else:
            print(f"Using existing user: {user.external_id} ({user.id})")

        # Create API key
        raw_key, api_key = await create_api_key(db, user_id=user.id, name=args.key_name)
        print("\nAPI Key created (shown once — save it now):")
        print(f"  Key:    {raw_key}")
        print(f"  Prefix: {api_key.key_prefix}")
        print(f"  ID:     {api_key.id}")
        print(f"\nUsage: Authorization: Bearer {raw_key}")


if __name__ == "__main__":
    asyncio.run(main())
