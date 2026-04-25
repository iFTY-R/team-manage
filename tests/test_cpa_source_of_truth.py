import unittest
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine as real_create_async_engine
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio

_original_create_async_engine = sqlalchemy_asyncio.create_async_engine
sqlalchemy_asyncio.create_async_engine = lambda *args, **kwargs: None

from app.database import Base
from app.models import Team, CPAService, CPAMotherAccount
from app.services.cpa import CPAServiceManager
from app.services.encryption import encryption_service
from app.services.team import TeamService
from app.routes.admin import team_import, update_team, batch_delete_teams, BulkActionRequest

sqlalchemy_asyncio.create_async_engine = _original_create_async_engine


class CPASourceOfTruthTests(unittest.IsolatedAsyncioTestCase):
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

    def test_normalize_management_base_url(self):
        manager = CPAServiceManager()
        self.assertEqual(
            manager.normalize_management_base_url("https://cpa.example.com"),
            "https://cpa.example.com/v0/management",
        )
        self.assertEqual(
            manager.normalize_management_base_url("https://cpa.example.com/v0/management"),
            "https://cpa.example.com/v0/management",
        )
        self.assertEqual(
            manager.normalize_management_base_url("https://cpa.example.com/v0/management/auth-files"),
            "https://cpa.example.com/v0/management",
        )

    async def test_delete_service_blocked_when_selected_mother_accounts_exist(self):
        async with self.session_factory() as session:
            service = CPAService(
                name="cpa",
                api_url="https://cpa.example.com/v0/management",
                api_token_encrypted=encryption_service.encrypt_token("token"),
                enabled=True,
            )
            session.add(service)
            await session.flush()

            mother = CPAMotherAccount(
                service_id=service.id,
                auth_file_name="alpha.json",
                provider="codex",
                selected=True,
            )
            session.add(mother)
            await session.commit()

            manager = CPAServiceManager()
            result = await manager.delete_service(service.id, session)
            self.assertFalse(result["success"])
            self.assertIn("关联母号", result["error"])

    async def test_list_auth_files_keeps_upstream_missing_selected_rows_visible(self):
        async with self.session_factory() as session:
            service = CPAService(
                name="cpa",
                api_url="https://cpa.example.com/v0/management",
                api_token_encrypted=encryption_service.encrypt_token("token"),
                enabled=True,
            )
            session.add(service)
            await session.flush()

            mother = CPAMotherAccount(
                service_id=service.id,
                auth_file_name="missing.json",
                provider="codex",
                email="missing@example.com",
                selected=True,
                upstream_missing=True,
            )
            session.add(mother)
            await session.commit()

            manager = CPAServiceManager()
            manager._request_json = AsyncMock(return_value={"success": True, "data": {"files": []}, "error": None})
            result = await manager.list_auth_files(service.id, session)
            self.assertTrue(result["success"])
            self.assertEqual(len(result["files"]), 1)
            self.assertEqual(result["files"][0]["name"], "missing.json")
            self.assertTrue(result["files"][0]["upstream_missing"])
            self.assertFalse(result["files"][0]["selectable"])

    async def test_deselecting_mother_account_retires_linked_team_projection(self):
        async with self.session_factory() as session:
            service = CPAService(
                name="cpa",
                api_url="https://cpa.example.com/v0/management",
                api_token_encrypted=encryption_service.encrypt_token("token"),
                enabled=True,
            )
            session.add(service)
            await session.flush()

            mother = CPAMotherAccount(
                service_id=service.id,
                auth_file_name="selected.json",
                provider="codex",
                email="owner@example.com",
                selected=True,
            )
            session.add(mother)
            await session.flush()

            team = Team(
                email="owner@example.com",
                access_token_encrypted=encryption_service.encrypt_token("access"),
                account_id="acct-2",
                source_type="cpa",
                cpa_service_id=service.id,
                cpa_auth_file_name="selected.json",
            )
            session.add(team)
            await session.commit()

            manager = CPAServiceManager()
            manager.list_auth_files = AsyncMock(return_value={"success": True, "files": [], "error": None})
            result = await manager.update_mother_account_selection(service.id, [], session)
            self.assertTrue(result["success"])

            saved_team = await session.scalar(select(Team).where(Team.id == team.id))
            saved_mother = await session.scalar(select(CPAMotherAccount).where(CPAMotherAccount.id == mother.id))
            self.assertEqual(saved_team.source_type, "legacy")
            self.assertEqual(saved_team.sync_status, "legacy_retired")
            self.assertFalse(saved_mother.selected)

    async def test_sync_projection_retires_stale_team_when_upstream_workspace_disappears(self):
        async with self.session_factory() as session:
            service = CPAService(
                name="cpa",
                api_url="https://cpa.example.com/v0/management",
                api_token_encrypted=encryption_service.encrypt_token("token"),
                enabled=True,
            )
            session.add(service)
            await session.flush()

            mother = CPAMotherAccount(
                service_id=service.id,
                auth_file_name="selected.json",
                provider="codex",
                email="owner@example.com",
                selected=True,
                access_token_encrypted=encryption_service.encrypt_token("access"),
            )
            session.add(mother)
            await session.flush()

            stale_team = Team(
                email="owner@example.com",
                access_token_encrypted=encryption_service.encrypt_token("access"),
                account_id="acct-stale",
                source_type="cpa",
                cpa_service_id=service.id,
                cpa_mother_account_id=mother.id,
                cpa_auth_file_name=mother.auth_file_name,
                sync_status="ready",
            )
            session.add(stale_team)
            await session.commit()

            manager = TeamService()
            manager.jwt_parser.is_token_expired = lambda token: False
            manager.jwt_parser.extract_email = lambda token: "owner@example.com"
            manager.chatgpt_service.get_account_info = AsyncMock(
                return_value={
                    "success": True,
                    "accounts": [
                        {
                            "account_id": "acct-live",
                            "name": "Live Team",
                            "plan_type": "team",
                            "subscription_plan": "team",
                            "expires_at": None,
                            "has_active_subscription": True,
                            "account_user_role": "owner",
                        }
                    ],
                }
            )
            manager.chatgpt_service.get_members = AsyncMock(return_value={"success": True, "total": 0})
            manager.chatgpt_service.get_invites = AsyncMock(return_value={"success": True, "total": 0})
            manager.chatgpt_service.get_account_settings = AsyncMock(return_value={"success": True, "data": {"beta_settings": {}}})

            result = await manager.sync_projection_from_cpa_record(
                record={"token": "valid-token", "email": "owner@example.com", "account_id": "acct-live"},
                cpa_service=service,
                mother_account=mother,
                db_session=session,
            )
            self.assertTrue(result["success"])

            retired = await session.scalar(select(Team).where(Team.id == stale_team.id))
            self.assertEqual(retired.source_type, "legacy")
            self.assertEqual(retired.sync_status, "upstream_missing")

    async def test_missing_auth_file_retires_linked_team_and_guard_blocks_runtime_use(self):
        async with self.session_factory() as session:
            service = CPAService(
                name="cpa",
                api_url="https://cpa.example.com/v0/management",
                api_token_encrypted=encryption_service.encrypt_token("token"),
                enabled=True,
            )
            session.add(service)
            await session.flush()

            mother = CPAMotherAccount(
                service_id=service.id,
                auth_file_name="missing.json",
                provider="codex",
                email="owner@example.com",
                selected=True,
            )
            session.add(mother)
            await session.flush()

            team = Team(
                email="owner@example.com",
                access_token_encrypted=encryption_service.encrypt_token("access"),
                account_id="acct-live",
                source_type="cpa",
                cpa_service_id=service.id,
                cpa_mother_account_id=mother.id,
                cpa_auth_file_name=mother.auth_file_name,
                sync_status="ready",
            )
            session.add(team)
            await session.commit()

            manager = CPAServiceManager()
            manager._download_auth_file = AsyncMock(
                return_value={"success": False, "status_code": 404, "error": "not found", "data": None}
            )
            result = await manager.sync_selected_accounts(service.id, session)
            self.assertFalse(result["success"])

            retired_team = await session.scalar(select(Team).where(Team.id == team.id))
            retired_mother = await session.scalar(select(CPAMotherAccount).where(CPAMotherAccount.id == mother.id))
            self.assertEqual(retired_team.source_type, "legacy")
            self.assertEqual(retired_team.sync_status, "upstream_missing")
            self.assertTrue(retired_mother.upstream_missing)

            service_manager = TeamService()
            guard_result = await service_manager.sync_team_info(team.id, session)
            self.assertFalse(guard_result["success"])
            self.assertIn("旧本地数据", guard_result["error"])

    async def test_cpa_team_info_is_redacted_and_update_blocks_local_credential_mutation(self):
        async with self.session_factory() as session:
            team = Team(
                email="owner@example.com",
                access_token_encrypted=encryption_service.encrypt_token("access"),
                refresh_token_encrypted=encryption_service.encrypt_token("refresh"),
                session_token_encrypted=encryption_service.encrypt_token("session"),
                client_id="client-id",
                account_id="acct-1",
                source_type="cpa",
                cpa_service_id=1,
                cpa_auth_file_name="alpha.json",
            )
            session.add(team)
            await session.commit()

            service = TeamService()
            info = await service.get_team_by_id(team.id, session)
            self.assertTrue(info["success"])
            self.assertNotIn("access_token", info["team"])
            self.assertTrue(info["team"]["has_cached_access_token"])
            self.assertEqual(info["team"]["source_type"], "cpa")

            update = await service.update_team(
                team.id,
                db_session=session,
                access_token="new-access",
            )
            self.assertFalse(update["success"])
            self.assertIn("不允许手工修改本地凭证", update["error"])

    async def test_sync_team_info_rejects_non_cpa_team(self):
        async with self.session_factory() as session:
            team = Team(
                email="legacy@example.com",
                access_token_encrypted=encryption_service.encrypt_token("access"),
                account_id="acct-legacy",
                source_type="legacy",
            )
            session.add(team)
            await session.commit()

            service = TeamService()
            result = await service.sync_team_info(team.id, session)
            self.assertFalse(result["success"])
            self.assertIn("旧本地数据", result["error"])

    async def test_admin_team_import_route_is_gone(self):
        response = await team_import(db=None, current_user={})
        self.assertEqual(response.status_code, 410)
        self.assertIn("本地 Team 凭证导入已下线", response.body.decode("utf-8"))

    async def test_admin_team_update_route_is_gone(self):
        response = await update_team(1, db=None, current_user={})
        self.assertEqual(response.status_code, 410)
        self.assertIn("本地 Team 编辑入口已下线", response.body.decode("utf-8"))

    async def test_admin_batch_delete_route_is_gone(self):
        response = await batch_delete_teams(BulkActionRequest(ids=[1]), db=None, current_user={})
        self.assertEqual(response.status_code, 410)
        self.assertIn("批量删除入口已下线", response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
