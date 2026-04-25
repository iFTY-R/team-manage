import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine as real_create_async_engine
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio

_original_create_async_engine = sqlalchemy_asyncio.create_async_engine
sqlalchemy_asyncio.create_async_engine = lambda *args, **kwargs: None

from app.database import Base
from app.models import Team
from app.services.team import TeamService

sqlalchemy_asyncio.create_async_engine = _original_create_async_engine


class TeamDefaultCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = real_create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_team_model_default_max_members_is_five(self):
        async with self.session_factory() as session:
            team = Team(
                email="owner@example.com",
                access_token_encrypted="enc",
                account_id="acct-default",
            )
            session.add(team)
            await session.commit()

            saved = await session.scalar(select(Team).where(Team.id == team.id))
            self.assertEqual(saved.max_members, 5)

    async def test_imported_team_uses_five_member_default_capacity(self):
        async with self.session_factory() as session:
            service = TeamService()
            service.jwt_parser.extract_email = lambda token: "owner@example.com"
            service.jwt_parser.is_token_expired = lambda token: False

            service.chatgpt_service.get_account_info = AsyncMock(
                return_value={
                    "success": True,
                    "accounts": [
                        {
                            "account_id": "acct-import",
                            "name": "Imported Team",
                            "plan_type": "team",
                            "subscription_plan": "team",
                            "expires_at": None,
                            "has_active_subscription": True,
                            "account_user_role": "owner",
                        }
                    ],
                }
            )
            service.chatgpt_service.get_members = AsyncMock(return_value={"success": True, "total": 0})
            service.chatgpt_service.get_invites = AsyncMock(return_value={"success": True, "total": 0})
            service.chatgpt_service.get_account_settings = AsyncMock(
                return_value={"success": True, "data": {"beta_settings": {}}}
            )

            with patch("app.services.team.encryption_service.encrypt_token", return_value="enc"):
                result = await service.import_team_single(
                    access_token="valid-token",
                    db_session=session,
                    email="owner@example.com",
                )

            self.assertTrue(result["success"])

            saved = await session.scalar(select(Team).where(Team.account_id == "acct-import"))
            self.assertIsNotNone(saved)
            self.assertEqual(saved.max_members, 5)


if __name__ == "__main__":
    unittest.main()
