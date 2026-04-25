"""
CPA 服务
负责 CPA 服务配置、auth-files 拉取、母号选择、同步到本地 Team 投影，以及后台定期同步。
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import CPAService, CPAMotherAccount, Team
from app.services.encryption import encryption_service
from app.services.settings import settings_service
from app.utils.time_utils import get_now
from app.utils.token_parser import TokenParser

logger = logging.getLogger(__name__)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00").replace("+00:00", ""))
    except Exception:
        return None


class CPAServiceManager:
    """CPA 服务管理器"""

    DEFAULT_SYNC_INTERVAL_SECONDS = 15 * 60

    def __init__(self):
        self.token_parser = TokenParser()
        self._sync_task: Optional[asyncio.Task] = None
        self._sync_lock = asyncio.Lock()

    @staticmethod
    def normalize_management_base_url(raw_url: str) -> str:
        """规范化管理 API 根地址"""
        value = (raw_url or "").strip().rstrip("/")
        if not value:
            raise ValueError("API URL 不能为空")

        lowered = value.lower()
        marker_auth_files = "/v0/management/auth-files"
        marker_management = "/v0/management"

        if marker_auth_files in lowered:
            idx = lowered.index(marker_auth_files)
            return value[:idx] + "/v0/management"

        if marker_management in lowered:
            idx = lowered.index(marker_management)
            return value[:idx] + "/v0/management"

        if lowered.endswith("/auth-files"):
            return value[: -len("/auth-files")] + "/v0/management"

        return value + "/v0/management"

    async def _effective_proxy(self, db_session: AsyncSession, service_proxy: Optional[str]) -> Optional[str]:
        if service_proxy and service_proxy.strip():
            return service_proxy.strip()
        proxy_config = await settings_service.get_proxy_config(db_session)
        if proxy_config["enabled"] and proxy_config["proxy"]:
            return proxy_config["proxy"]
        return None

    async def _request_json(
        self,
        db_session: AsyncSession,
        service: CPAService,
        method: str,
        path: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_url = self.normalize_management_base_url(service.api_url)
        token = encryption_service.decrypt_token(service.api_token_encrypted)
        proxy = await self._effective_proxy(db_session, service.proxy)
        session = CurlAsyncSession(
            timeout=30,
            verify=False,
            proxies={"http": proxy, "https": proxy} if proxy else None,
        )
        url = f"{base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = await session.request(method.upper(), url, headers=headers, json=json_data, params=params)
            body_text = response.text
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}

            if response.status_code >= 400:
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": payload.get("error") or payload.get("message") or body_text or f"HTTP {response.status_code}",
                    "data": payload,
                }

            return {"success": True, "status_code": response.status_code, "data": payload, "error": None}
        except Exception as e:
            return {"success": False, "status_code": 0, "error": str(e), "data": None}
        finally:
            await session.close()

    def serialize_service(self, service: CPAService) -> Dict[str, Any]:
        return {
            "id": service.id,
            "name": service.name,
            "api_url": service.api_url,
            "proxy": service.proxy or "",
            "enabled": service.enabled,
            "has_api_token": bool(service.api_token_encrypted),
            "last_tested_at": service.last_tested_at.isoformat() if service.last_tested_at else None,
            "last_test_status": service.last_test_status,
            "last_test_message": service.last_test_message,
            "created_at": service.created_at.isoformat() if service.created_at else None,
            "updated_at": service.updated_at.isoformat() if service.updated_at else None,
        }

    def serialize_mother_account(self, item: CPAMotherAccount) -> Dict[str, Any]:
        return {
            "id": item.id,
            "service_id": item.service_id,
            "auth_file_name": item.auth_file_name,
            "provider": item.provider,
            "email": item.email,
            "label": item.label,
            "source": item.source,
            "runtime_only": item.runtime_only,
            "selected": item.selected,
            "upstream_status": item.upstream_status,
            "upstream_status_message": item.upstream_status_message,
            "upstream_missing": item.upstream_missing,
            "last_upstream_refresh_at": item.last_upstream_refresh_at.isoformat() if item.last_upstream_refresh_at else None,
            "last_sync": item.last_sync.isoformat() if item.last_sync else None,
            "sync_status": item.sync_status,
            "sync_error": item.sync_error,
        }

    async def list_services(self, db_session: AsyncSession) -> List[Dict[str, Any]]:
        result = await db_session.execute(select(CPAService).order_by(CPAService.created_at.desc()))
        return [self.serialize_service(item) for item in result.scalars().all()]

    async def get_service(self, service_id: int, db_session: AsyncSession) -> Optional[CPAService]:
        result = await db_session.execute(select(CPAService).where(CPAService.id == service_id))
        return result.scalar_one_or_none()

    async def create_service(
        self,
        *,
        name: str,
        api_url: str,
        api_token: str,
        proxy: str,
        enabled: bool,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        try:
            normalized_url = self.normalize_management_base_url(api_url)
            if not api_token.strip():
                return {"success": False, "error": "API Token 不能为空"}

            existing = await db_session.scalar(select(CPAService).where(func.lower(CPAService.name) == name.strip().lower()))
            if existing:
                return {"success": False, "error": "服务名称已存在"}

            service = CPAService(
                name=name.strip(),
                api_url=normalized_url,
                api_token_encrypted=encryption_service.encrypt_token(api_token.strip()),
                proxy=proxy.strip() or None,
                enabled=enabled,
            )
            db_session.add(service)
            await db_session.commit()
            await db_session.refresh(service)
            return {"success": True, "service": self.serialize_service(service)}
        except Exception as e:
            await db_session.rollback()
            logger.error(f"创建 CPA 服务失败: {e}")
            return {"success": False, "error": f"创建失败: {e}"}

    async def update_service(
        self,
        service_id: int,
        *,
        name: str,
        api_url: str,
        api_token: Optional[str],
        proxy: str,
        enabled: bool,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        service = await self.get_service(service_id, db_session)
        if not service:
            return {"success": False, "error": "CPA 服务不存在"}

        try:
            normalized_url = self.normalize_management_base_url(api_url)
            existing = await db_session.scalar(
                select(CPAService).where(func.lower(CPAService.name) == name.strip().lower(), CPAService.id != service_id)
            )
            if existing:
                return {"success": False, "error": "服务名称已存在"}

            service.name = name.strip()
            service.api_url = normalized_url
            service.proxy = proxy.strip() or None
            service.enabled = enabled
            if api_token and api_token.strip():
                service.api_token_encrypted = encryption_service.encrypt_token(api_token.strip())
            await db_session.commit()
            await db_session.refresh(service)
            return {"success": True, "service": self.serialize_service(service)}
        except Exception as e:
            await db_session.rollback()
            logger.error(f"更新 CPA 服务失败: {e}")
            return {"success": False, "error": f"更新失败: {e}"}

    async def delete_service(self, service_id: int, db_session: AsyncSession) -> Dict[str, Any]:
        service = await self.get_service(service_id, db_session)
        if not service:
            return {"success": False, "error": "CPA 服务不存在"}

        active_links = await db_session.scalar(
            select(func.count(CPAMotherAccount.id)).where(
                CPAMotherAccount.service_id == service_id,
                CPAMotherAccount.selected.is_(True),
            )
        )
        if active_links and active_links > 0:
            return {"success": False, "error": "该服务仍有关联母号，请先停用并取消选择后再删除"}

        try:
            await db_session.delete(service)
            await db_session.commit()
            return {"success": True, "message": "CPA 服务已删除"}
        except Exception as e:
            await db_session.rollback()
            logger.error(f"删除 CPA 服务失败: {e}")
            return {"success": False, "error": f"删除失败: {e}"}

    async def test_service(self, service_id: int, db_session: AsyncSession) -> Dict[str, Any]:
        service = await self.get_service(service_id, db_session)
        if not service:
            return {"success": False, "error": "CPA 服务不存在"}

        result = await self._request_json(db_session, service, "GET", "/auth-files")
        service.last_tested_at = get_now()
        service.last_test_status = "success" if result["success"] else "error"
        service.last_test_message = (
            f"连接成功，返回 {len(result['data'].get('files', []))} 条凭据"
            if result["success"]
            else result["error"]
        )
        await db_session.commit()

        if result["success"]:
            return {
                "success": True,
                "message": service.last_test_message,
                "files_count": len(result["data"].get("files", [])),
            }
        return {"success": False, "error": result["error"]}

    async def list_auth_files(self, service_id: int, db_session: AsyncSession) -> Dict[str, Any]:
        service = await self.get_service(service_id, db_session)
        if not service:
            return {"success": False, "error": "CPA 服务不存在", "files": []}

        result = await self._request_json(db_session, service, "GET", "/auth-files")
        if not result["success"]:
            return {"success": False, "error": result["error"], "files": []}

        selected_result = await db_session.execute(
            select(CPAMotherAccount).where(CPAMotherAccount.service_id == service_id)
        )
        selected_map = {item.auth_file_name: item for item in selected_result.scalars().all()}
        files = []
        for item in result["data"].get("files", []):
            if item.get("provider") != "codex":
                continue
            name = item.get("name")
            source = item.get("source")
            runtime_only = bool(item.get("runtime_only"))
            selectable = bool(name) and source == "file" and not runtime_only
            existing = selected_map.get(name)
            files.append(
                {
                    "name": name,
                    "provider": item.get("provider"),
                    "label": item.get("label"),
                    "email": item.get("email"),
                    "status": item.get("status"),
                    "status_message": item.get("status_message"),
                    "disabled": bool(item.get("disabled")),
                    "unavailable": bool(item.get("unavailable")),
                    "runtime_only": runtime_only,
                    "source": source,
                    "last_refresh": item.get("last_refresh"),
                    "selected": bool(existing.selected) if existing else False,
                    "selectable": selectable,
                    "not_selectable_reason": None
                    if selectable
                    else ("runtime_only 凭据无法下载" if runtime_only else "仅支持 source=file 的条目"),
                    "sync_status": existing.sync_status if existing else None,
                    "sync_error": existing.sync_error if existing else None,
                    "upstream_missing": existing.upstream_missing if existing else False,
                }
            )
        for name, existing in selected_map.items():
            if name in {item["name"] for item in files}:
                continue
            files.append(
                {
                    "name": name,
                    "provider": existing.provider,
                    "label": existing.label,
                    "email": existing.email,
                    "status": existing.upstream_status,
                    "status_message": existing.upstream_status_message or "上游文件不存在或已重命名",
                    "disabled": False,
                    "unavailable": True,
                    "runtime_only": existing.runtime_only,
                    "source": existing.source,
                    "last_refresh": existing.last_upstream_refresh_at.isoformat() if existing.last_upstream_refresh_at else None,
                    "selected": bool(existing.selected),
                    "selectable": False,
                    "not_selectable_reason": "上游文件不存在或已重命名",
                    "sync_status": existing.sync_status,
                    "sync_error": existing.sync_error,
                    "upstream_missing": True,
                }
            )
        files.sort(key=lambda x: (x["email"] or "", x["name"] or ""))
        return {"success": True, "files": files, "error": None}

    async def update_mother_account_selection(
        self,
        service_id: int,
        selected_names: List[str],
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        service = await self.get_service(service_id, db_session)
        if not service:
            return {"success": False, "error": "CPA 服务不存在"}

        files_result = await self.list_auth_files(service_id, db_session)
        if not files_result["success"]:
            return files_result

        files_by_name = {item["name"]: item for item in files_result["files"]}
        normalized_names = sorted({name.strip() for name in selected_names if name and name.strip()})
        for name in normalized_names:
            file_item = files_by_name.get(name)
            if not file_item:
                return {"success": False, "error": f"未找到凭据文件: {name}"}
            if not file_item["selectable"]:
                return {"success": False, "error": f"{name} 不可选: {file_item['not_selectable_reason']}"}

        existing_rows = await db_session.execute(select(CPAMotherAccount).where(CPAMotherAccount.service_id == service_id))
        existing_map = {item.auth_file_name: item for item in existing_rows.scalars().all()}

        try:
            for name, row in existing_map.items():
                if name not in normalized_names:
                    row.selected = False
                    row.sync_status = "deselected"
                    row.sync_error = None
                    linked_teams = await db_session.execute(
                        select(Team).where(
                            Team.cpa_service_id == service_id,
                            Team.cpa_auth_file_name == name,
                            Team.source_type == "cpa",
                        )
                    )
                    for team in linked_teams.scalars().all():
                        team.source_type = "legacy"
                        team.sync_status = "legacy_retired"
                        team.sync_error = "对应母号已取消选择"

            for name in normalized_names:
                file_item = files_by_name[name]
                row = existing_map.get(name)
                if not row:
                    row = CPAMotherAccount(
                        service_id=service_id,
                        auth_file_name=name,
                        provider=file_item["provider"] or "codex",
                    )
                    db_session.add(row)
                row.email = file_item["email"]
                row.label = file_item["label"]
                row.source = file_item["source"]
                row.runtime_only = file_item["runtime_only"]
                row.selected = True
                row.upstream_status = file_item["status"]
                row.upstream_status_message = file_item["status_message"]
                row.last_upstream_refresh_at = _parse_dt(file_item["last_refresh"])
                row.upstream_missing = False
                if row.sync_status in (None, "deselected"):
                    row.sync_status = "idle"
                row.sync_error = None

            await db_session.commit()
            return {"success": True, "message": f"已保存 {len(normalized_names)} 个母号选择"}
        except Exception as e:
            await db_session.rollback()
            logger.error(f"保存母号选择失败: {e}")
            return {"success": False, "error": f"保存失败: {e}"}

    async def _download_auth_file(
        self,
        service: CPAService,
        auth_file_name: str,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        return await self._request_json(
            db_session,
            service,
            "GET",
            "/auth-files/download",
            params={"name": auth_file_name},
        )

    async def sync_selected_accounts(self, service_id: int, db_session: AsyncSession) -> Dict[str, Any]:
        service = await self.get_service(service_id, db_session)
        if not service:
            return {"success": False, "error": "CPA 服务不存在"}
        if not service.enabled:
            return {"success": False, "error": "CPA 服务已禁用"}

        selected_rows = await db_session.execute(
            select(CPAMotherAccount).where(
                CPAMotherAccount.service_id == service_id,
                CPAMotherAccount.selected.is_(True),
            )
        )
        rows = selected_rows.scalars().all()
        if not rows:
            return {"success": False, "error": "尚未选择任何母号"}

        async with self._sync_lock:
            success_count = 0
            failed_count = 0
            results = []
            from app.services.team import team_service

            for row in rows:
                download_result = await self._download_auth_file(service, row.auth_file_name, db_session)
                if not download_result["success"]:
                    row.sync_status = "error"
                    row.sync_error = download_result["error"]
                    row.last_sync = get_now()
                    if download_result.get("status_code") == 404:
                        row.upstream_missing = True
                        stale_teams_result = await db_session.execute(
                            select(Team).where(
                                Team.source_type == "cpa",
                                Team.cpa_service_id == service_id,
                                Team.cpa_auth_file_name == row.auth_file_name,
                            )
                        )
                        for stale_team in stale_teams_result.scalars().all():
                            stale_team.source_type = "legacy"
                            stale_team.sync_status = "upstream_missing"
                            stale_team.sync_error = "对应的 CPA auth-file 已不存在或已重命名"
                    results.append({"name": row.auth_file_name, "success": False, "error": download_result["error"]})
                    failed_count += 1
                    continue

                try:
                    records = self.token_parser.parse_team_import_content(json.dumps(download_result["data"], ensure_ascii=False))
                    if not records:
                        raise ValueError("下载到的凭据内容为空")
                    record = records[0]
                    row.access_token_encrypted = (
                        encryption_service.encrypt_token(record["token"]) if record.get("token") else None
                    )
                    row.refresh_token_encrypted = (
                        encryption_service.encrypt_token(record["refresh_token"]) if record.get("refresh_token") else None
                    )
                    row.session_token_encrypted = (
                        encryption_service.encrypt_token(record["session_token"]) if record.get("session_token") else None
                    )
                    row.client_id = record.get("client_id")
                    sync_result = await team_service.sync_projection_from_cpa_record(
                        record=record,
                        cpa_service=service,
                        mother_account=row,
                        db_session=db_session,
                    )
                    row.last_sync = get_now()
                    row.last_upstream_refresh_at = _parse_dt(records[0].get("last_refresh") or download_result["data"].get("last_refresh"))
                    row.sync_status = "ready" if sync_result["success"] else "error"
                    row.sync_error = sync_result.get("error")
                    row.upstream_missing = False
                    results.append({"name": row.auth_file_name, **sync_result})
                    if sync_result["success"]:
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    row.last_sync = get_now()
                    row.sync_status = "error"
                    row.sync_error = str(e)
                    results.append({"name": row.auth_file_name, "success": False, "error": str(e)})
                    failed_count += 1

            legacy_rows = await db_session.execute(
                select(Team).where(Team.source_type != "cpa")
            )
            for team in legacy_rows.scalars().all():
                team.source_type = "legacy"
                if not team.sync_status or team.sync_status == "idle":
                    team.sync_status = "legacy_retired"

            await db_session.commit()
            return {
                "success": failed_count == 0,
                "message": f"同步完成，成功 {success_count} 个，失败 {failed_count} 个",
                "success_count": success_count,
                "failed_count": failed_count,
                "results": results,
            }

    async def sync_all_enabled_services(self) -> None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(CPAService).where(CPAService.enabled.is_(True)))
            services = result.scalars().all()
            for service in services:
                try:
                    await self.sync_selected_accounts(service.id, session)
                except Exception as e:
                    logger.error(f"后台同步 CPA 服务 {service.id} 失败: {e}")

    async def _periodic_sync_loop(self):
        while True:
            try:
                await self.sync_all_enabled_services()
            except Exception as e:
                logger.error(f"后台定期同步失败: {e}")
            await asyncio.sleep(self.DEFAULT_SYNC_INTERVAL_SECONDS)

    def start_background_sync(self):
        if self._sync_task and not self._sync_task.done():
            return
        self._sync_task = asyncio.create_task(self._periodic_sync_loop())

    async def stop_background_sync(self):
        if not self._sync_task:
            return
        self._sync_task.cancel()
        try:
            await self._sync_task
        except asyncio.CancelledError:
            pass
        self._sync_task = None


cpa_service_manager = CPAServiceManager()
