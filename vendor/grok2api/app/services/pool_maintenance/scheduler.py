"""Automated pool maintenance: prune bad tokens and create register tasks."""

from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional

import aiohttp

from app.core.config import get_config
from app.core.logger import logger
from app.services.token.manager import get_token_manager

MANAGED_TASK_NOTE = "managed_by=grok2api_pool_maintenance"
RUNNING_TASK_STATUSES = {"queued", "running", "stopping"}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _coerce_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, result)


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _coerce_string_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = list(default)
    cleaned = [item for item in items if item]
    return cleaned or list(default)


@dataclass
class PoolMaintenanceSettings:
    enabled: bool
    console_url: str
    interval_sec: int
    managed_pools: list[str]
    min_active_tokens: int
    register_count_per_task: int
    max_running_tasks: int
    task_name_prefix: str
    delete_cooling_tokens: bool
    delete_expired_tokens: bool
    proxy: str
    browser_proxy: str
    temp_mail_api_base: str
    temp_mail_admin_password: str
    temp_mail_domain: str
    temp_mail_site_password: str
    api_endpoint: str
    api_token: str
    api_append: bool
    api_auto_enable_nsfw: bool

    @classmethod
    def from_config(cls) -> "PoolMaintenanceSettings":
        api_token = _coerce_str(get_config("pool_maintenance.api_token", ""))
        if not api_token:
            api_token = _coerce_str(get_config("app.app_key", ""))
        api_endpoint = _coerce_str(get_config("pool_maintenance.api_endpoint", ""))
        if not api_endpoint:
            api_endpoint = "http://grok2api:8000/v1/admin/tokens"

        return cls(
            enabled=_coerce_bool(get_config("pool_maintenance.enabled", False)),
            console_url=_coerce_str(get_config("pool_maintenance.console_url", "http://console:18600")).rstrip("/"),
            interval_sec=_coerce_int(get_config("pool_maintenance.interval_sec", 60), 60, 5),
            managed_pools=_coerce_string_list(
                get_config("pool_maintenance.managed_pools", ["ssoBasic"]),
                ["ssoBasic"],
            ),
            min_active_tokens=_coerce_int(get_config("pool_maintenance.min_active_tokens", 30), 30, 1),
            register_count_per_task=_coerce_int(
                get_config("pool_maintenance.register_count_per_task", 20),
                20,
                1,
            ),
            max_running_tasks=_coerce_int(get_config("pool_maintenance.max_running_tasks", 1), 1, 1),
            task_name_prefix=_coerce_str(get_config("pool_maintenance.task_name_prefix", "pool-maintenance")) or "pool-maintenance",
            delete_cooling_tokens=_coerce_bool(get_config("pool_maintenance.delete_cooling_tokens", True), True),
            delete_expired_tokens=_coerce_bool(get_config("pool_maintenance.delete_expired_tokens", True), True),
            proxy=_coerce_str(get_config("pool_maintenance.proxy", "")),
            browser_proxy=_coerce_str(get_config("pool_maintenance.browser_proxy", "")),
            temp_mail_api_base=_coerce_str(get_config("pool_maintenance.temp_mail_api_base", "")),
            temp_mail_admin_password=_coerce_str(get_config("pool_maintenance.temp_mail_admin_password", "")),
            temp_mail_domain=_coerce_str(get_config("pool_maintenance.temp_mail_domain", "")),
            temp_mail_site_password=_coerce_str(get_config("pool_maintenance.temp_mail_site_password", "")),
            api_endpoint=api_endpoint,
            api_token=api_token,
            api_append=_coerce_bool(get_config("pool_maintenance.api_append", True), True),
            api_auto_enable_nsfw=_coerce_bool(get_config("pool_maintenance.api_auto_enable_nsfw", False), False),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


class PoolMaintenanceScheduler:
    """Background scheduler for maintaining token pool size."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._wake_event = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._last_result: dict[str, Any] | None = None
        self._last_error: str = ""
        self._last_run_started_at: str = ""
        self._last_run_finished_at: str = ""

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("PoolMaintenance: background task started")

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._wake_event.set()
        if self._task:
            self._task.cancel()
        logger.info("PoolMaintenance: background task stopped")

    def poke(self):
        self._wake_event.set()

    def snapshot(self) -> dict[str, Any]:
        return {
            "worker_running": self._running,
            "last_run_started_at": self._last_run_started_at,
            "last_run_finished_at": self._last_run_finished_at,
            "last_error": self._last_error,
            "last_result": self._last_result,
        }

    async def _loop(self):
        while self._running:
            settings = PoolMaintenanceSettings.from_config()
            if settings.enabled:
                try:
                    await self.run_once(trigger="scheduler")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.exception(f"PoolMaintenance: scheduled run failed: {exc}")

            interval = PoolMaintenanceSettings.from_config().interval_sec
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            finally:
                self._wake_event.clear()

    async def _fetch_console_tasks(self, settings: PoolMaintenanceSettings) -> list[dict[str, Any]]:
        if not settings.console_url:
            return []
        url = f"{settings.console_url}/api/tasks"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(f"Console task list failed: HTTP {response.status} {text[:200]}")
                data = await response.json()
        tasks = data.get("tasks") if isinstance(data, dict) else []
        return tasks if isinstance(tasks, list) else []

    def _is_managed_task(self, task: dict[str, Any], settings: PoolMaintenanceSettings) -> bool:
        notes = _coerce_str(task.get("notes", ""))
        name = _coerce_str(task.get("name", ""))
        return MANAGED_TASK_NOTE in notes or name.startswith(settings.task_name_prefix)

    async def _create_console_task(
        self,
        settings: PoolMaintenanceSettings,
        count: int,
    ) -> dict[str, Any]:
        if not settings.console_url:
            raise RuntimeError("Console URL is empty")

        payload: dict[str, Any] = {
            "name": f"{settings.task_name_prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "count": count,
            "notes": MANAGED_TASK_NOTE,
            "api_append": settings.api_append,
            "api_auto_enable_nsfw": settings.api_auto_enable_nsfw,
        }
        optional_fields = {
            "proxy": settings.proxy,
            "browser_proxy": settings.browser_proxy,
            "temp_mail_api_base": settings.temp_mail_api_base,
            "temp_mail_admin_password": settings.temp_mail_admin_password,
            "temp_mail_domain": settings.temp_mail_domain,
            "temp_mail_site_password": settings.temp_mail_site_password,
            "api_endpoint": settings.api_endpoint,
            "api_token": settings.api_token,
        }
        for key, value in optional_fields.items():
            if value:
                payload[key] = value

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{settings.console_url}/api/tasks",
                json=payload,
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(f"Console create task failed: HTTP {response.status} {text[:200]}")
                data = await response.json()
        task = data.get("task") if isinstance(data, dict) else None
        if not isinstance(task, dict):
            raise RuntimeError("Console create task returned invalid payload")
        return task

    async def _collect_runtime(self, settings: PoolMaintenanceSettings) -> dict[str, Any]:
        manager = await get_token_manager()
        pools: dict[str, dict[str, int]] = {}
        removable_tokens: list[dict[str, str]] = []
        active_total = 0
        total_tokens = 0

        for pool_name in settings.managed_pools:
            tokens = manager.get_pool_tokens(pool_name)
            counts = {
                "total": 0,
                "active": 0,
                "cooling": 0,
                "expired": 0,
                "disabled": 0,
                "other": 0,
            }
            for token in tokens:
                counts["total"] += 1
                total_tokens += 1
                raw_status = getattr(token, "status", "other")
                status = str(getattr(raw_status, "value", raw_status) or "other")
                if status == "active":
                    counts["active"] += 1
                    active_total += 1
                elif status == "cooling":
                    counts["cooling"] += 1
                    if settings.delete_cooling_tokens:
                        removable_tokens.append({"pool": pool_name, "token": token.token, "status": status})
                elif status == "expired":
                    counts["expired"] += 1
                    if settings.delete_expired_tokens:
                        removable_tokens.append({"pool": pool_name, "token": token.token, "status": status})
                elif status == "disabled":
                    counts["disabled"] += 1
                else:
                    counts["other"] += 1
            pools[pool_name] = counts

        managed_tasks: list[dict[str, Any]] = []
        running_task_count = 0
        console_error = ""
        try:
            tasks = await self._fetch_console_tasks(settings)
            for task in tasks:
                if not isinstance(task, dict) or not self._is_managed_task(task, settings):
                    continue
                managed_tasks.append(
                    {
                        "id": task.get("id"),
                        "name": task.get("name"),
                        "status": task.get("status"),
                        "target_count": task.get("target_count"),
                        "completed_count": task.get("completed_count"),
                        "failed_count": task.get("failed_count"),
                        "current_round": task.get("current_round"),
                        "current_phase": task.get("current_phase"),
                        "created_at": task.get("created_at"),
                        "started_at": task.get("started_at"),
                        "finished_at": task.get("finished_at"),
                    }
                )
            running_task_count = sum(
                1 for task in managed_tasks if str(task.get("status", "")) in RUNNING_TASK_STATUSES
            )
        except Exception as exc:
            console_error = str(exc)

        managed_tasks.sort(key=lambda item: (item.get("id") or 0), reverse=True)

        return {
            "token_summary": {
                "active_total": active_total,
                "total_tokens": total_tokens,
                "removable_total": len(removable_tokens),
                "pools": pools,
            },
            "removable_tokens": [
                {
                    "pool": item["pool"],
                    "status": item["status"],
                    "token_preview": f"{item['token'][:8]}...{item['token'][-8:]}" if len(item["token"]) > 20 else item["token"],
                }
                for item in removable_tokens
            ],
            "raw_removals": removable_tokens,
            "managed_tasks": managed_tasks[:20],
            "running_task_count": running_task_count,
            "console_error": console_error,
        }

    async def get_status(self) -> dict[str, Any]:
        settings = PoolMaintenanceSettings.from_config()
        runtime = await self._collect_runtime(settings)
        return {
            "config": settings.to_public_dict(),
            "runtime": runtime,
            **self.snapshot(),
        }

    async def run_once(self, trigger: str = "manual") -> dict[str, Any]:
        async with self._run_lock:
            settings = PoolMaintenanceSettings.from_config()
            self._last_run_started_at = _now_iso()
            self._last_error = ""
            try:
                before = await self._collect_runtime(settings)
                manager = await get_token_manager()

                removed: list[dict[str, str]] = []
                for item in before["raw_removals"]:
                    if await manager.remove(item["token"]):
                        removed.append({"pool": item["pool"], "status": item["status"]})

                after_cleanup = await self._collect_runtime(settings)
                missing = max(
                    0,
                    settings.min_active_tokens - int(after_cleanup["token_summary"]["active_total"]),
                )
                slots = max(0, settings.max_running_tasks - int(after_cleanup["running_task_count"]))
                created_tasks: list[dict[str, Any]] = []
                allow_create_tasks = trigger != "scheduler" or settings.enabled

                if allow_create_tasks and missing > 0 and slots > 0:
                    task_count = max(1, settings.register_count_per_task)
                    task_num = min(slots, math.ceil(missing / task_count))
                    for _ in range(task_num):
                        created = await self._create_console_task(settings, task_count)
                        created_tasks.append(
                            {
                                "id": created.get("id"),
                                "name": created.get("name"),
                                "status": created.get("status"),
                                "target_count": created.get("target_count"),
                            }
                        )

                final_runtime = await self._collect_runtime(settings)
                result = {
                    "trigger": trigger,
                    "ran_at": _now_iso(),
                    "removed_count": len(removed),
                    "removed_status_counts": {
                        "cooling": sum(1 for item in removed if item["status"] == "cooling"),
                        "expired": sum(1 for item in removed if item["status"] == "expired"),
                    },
                    "created_task_count": len(created_tasks),
                    "created_tasks": created_tasks,
                    "missing_before_create": missing,
                    "active_total_before": before["token_summary"]["active_total"],
                    "active_total_after_cleanup": after_cleanup["token_summary"]["active_total"],
                    "active_total_after_run": final_runtime["token_summary"]["active_total"],
                    "running_tasks_after_run": final_runtime["running_task_count"],
                    "console_error": final_runtime["console_error"],
                }
                self._last_result = result
                self._last_error = final_runtime["console_error"]

                logger.info(
                    f"PoolMaintenance: trigger={trigger} removed={result['removed_count']} "
                    f"created_tasks={result['created_task_count']} "
                    f"active_after={result['active_total_after_run']}"
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                raise
            finally:
                self._last_run_finished_at = _now_iso()


_scheduler: Optional[PoolMaintenanceScheduler] = None


def get_scheduler() -> PoolMaintenanceScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PoolMaintenanceScheduler()
    return _scheduler
