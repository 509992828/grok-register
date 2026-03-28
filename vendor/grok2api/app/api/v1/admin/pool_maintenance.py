from __future__ import annotations

from typing import Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_app_key
from app.core.config import config
from app.services.pool_maintenance import get_scheduler

router = APIRouter()

SECRET_FIELDS = {
    "temp_mail_admin_password",
    "temp_mail_site_password",
    "api_token",
}


def _normalize_string_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = list(default)
    cleaned = [item for item in items if item]
    return cleaned or list(default)


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    def str_value(key: str, default: str = "") -> str:
        value = data.get(key, default)
        return "" if value is None else str(value).strip()

    def int_value(key: str, default: int, minimum: int) -> int:
        try:
            return max(minimum, int(data.get(key, default)))
        except (TypeError, ValueError):
            return default

    def bool_value(key: str, default: bool) -> bool:
        value = data.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    cleaned = {
        "enabled": bool_value("enabled", False),
        "console_url": str_value("console_url", "http://console:18600").rstrip("/"),
        "interval_sec": int_value("interval_sec", 60, 5),
        "managed_pools": _normalize_string_list(data.get("managed_pools", ["ssoBasic"]), ["ssoBasic"]),
        "min_active_tokens": int_value("min_active_tokens", 30, 1),
        "register_count_per_task": int_value("register_count_per_task", 20, 1),
        "max_running_tasks": int_value("max_running_tasks", 1, 1),
        "task_name_prefix": str_value("task_name_prefix", "pool-maintenance") or "pool-maintenance",
        "delete_cooling_tokens": bool_value("delete_cooling_tokens", True),
        "delete_expired_tokens": bool_value("delete_expired_tokens", True),
        "proxy": str_value("proxy"),
        "browser_proxy": str_value("browser_proxy"),
        "temp_mail_api_base": str_value("temp_mail_api_base"),
        "temp_mail_admin_password": str_value("temp_mail_admin_password"),
        "temp_mail_domain": str_value("temp_mail_domain"),
        "temp_mail_site_password": str_value("temp_mail_site_password"),
        "api_endpoint": str_value("api_endpoint", "http://grok2api:8000/v1/admin/tokens"),
        "api_token": str_value("api_token"),
        "api_append": bool_value("api_append", True),
        "api_auto_enable_nsfw": bool_value("api_auto_enable_nsfw", False),
    }
    return {"pool_maintenance": cleaned}


def _normalize_console_defaults(data: dict[str, Any] | None) -> dict[str, Any]:
    defaults = data if isinstance(data, dict) else {}
    api_conf = defaults.get("api") if isinstance(defaults.get("api"), dict) else {}
    return {
        "proxy": str(defaults.get("proxy", "") or "").strip(),
        "browser_proxy": str(defaults.get("browser_proxy", "") or "").strip(),
        "temp_mail_api_base": str(defaults.get("temp_mail_api_base", "") or "").strip(),
        "temp_mail_admin_password": str(defaults.get("temp_mail_admin_password", "") or "").strip(),
        "temp_mail_domain": str(defaults.get("temp_mail_domain", "") or "").strip(),
        "temp_mail_site_password": str(defaults.get("temp_mail_site_password", "") or "").strip(),
        "api_endpoint": str(api_conf.get("endpoint", "") or "").strip(),
        "api_token": str(api_conf.get("token", "") or "").strip(),
        "api_append": bool(api_conf.get("append", True)),
        "api_auto_enable_nsfw": bool(api_conf.get("auto_enable_nsfw", False)),
    }


def _merge_effective_task_config(
    pool_config: dict[str, Any],
    console_defaults: dict[str, Any] | None,
) -> dict[str, Any]:
    defaults = console_defaults if isinstance(console_defaults, dict) else {}
    return {
        "proxy": str(pool_config.get("proxy", "") or "").strip() or str(defaults.get("proxy", "") or "").strip(),
        "browser_proxy": str(pool_config.get("browser_proxy", "") or "").strip() or str(defaults.get("browser_proxy", "") or "").strip(),
        "temp_mail_api_base": str(pool_config.get("temp_mail_api_base", "") or "").strip() or str(defaults.get("temp_mail_api_base", "") or "").strip(),
        "temp_mail_admin_password": str(pool_config.get("temp_mail_admin_password", "") or "").strip() or str(defaults.get("temp_mail_admin_password", "") or "").strip(),
        "temp_mail_domain": str(pool_config.get("temp_mail_domain", "") or "").strip() or str(defaults.get("temp_mail_domain", "") or "").strip(),
        "temp_mail_site_password": str(pool_config.get("temp_mail_site_password", "") or "").strip() or str(defaults.get("temp_mail_site_password", "") or "").strip(),
        "api_endpoint": str(pool_config.get("api_endpoint", "") or "").strip() or str(defaults.get("api_endpoint", "") or "").strip(),
        "api_token": str(pool_config.get("api_token", "") or "").strip() or str(defaults.get("api_token", "") or "").strip(),
        "api_append": bool(pool_config.get("api_append", True)),
        "api_auto_enable_nsfw": bool(pool_config.get("api_auto_enable_nsfw", defaults.get("api_auto_enable_nsfw", False))),
    }


async def _fetch_console_defaults(console_url: str) -> tuple[dict[str, Any] | None, str]:
    normalized_url = str(console_url or "").strip().rstrip("/")
    if not normalized_url:
        return None, "Console URL is empty"

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{normalized_url}/api/settings") as response:
                if response.status >= 400:
                    text = await response.text()
                    return None, f"HTTP {response.status}: {text[:200]}"
                data = await response.json()
    except Exception as exc:
        return None, str(exc)

    defaults = data.get("defaults") if isinstance(data, dict) else None
    return _normalize_console_defaults(defaults), ""


@router.get("/pool-maintenance", dependencies=[Depends(verify_app_key)])
async def get_pool_maintenance():
    scheduler = get_scheduler()
    status = await scheduler.get_status()
    console_defaults, console_defaults_error = await _fetch_console_defaults(
        str((status.get("config") or {}).get("console_url", ""))
    )
    status["console_defaults"] = console_defaults
    status["console_defaults_error"] = console_defaults_error
    status["effective_task_config"] = _merge_effective_task_config(
        status.get("config") or {},
        console_defaults,
    )
    status["secret_fields"] = sorted(SECRET_FIELDS)
    return status


@router.post("/pool-maintenance", dependencies=[Depends(verify_app_key)])
async def update_pool_maintenance(data: dict[str, Any]):
    try:
        await config.update(_sanitize_payload(data))
        scheduler = get_scheduler()
        scheduler.poke()
        return {"status": "success", "message": "号池维护配置已更新"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pool-maintenance/run", dependencies=[Depends(verify_app_key)])
async def run_pool_maintenance():
    try:
        scheduler = get_scheduler()
        result = await scheduler.run_once(trigger="manual")
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
