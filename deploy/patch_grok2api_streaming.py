#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatchSpec:
    path: Path
    marker: str
    old: str
    new: str
    label: str


IMAGE_PATH = Path(
    os.getenv(
        "GROK2API_IMAGE_SERVICE_PATH",
        "/app/app/services/grok/services/image.py",
    )
)
IMAGINE_PATH = Path(
    os.getenv(
        "GROK2API_IMAGINE_API_PATH",
        "/app/app/api/v1/function/imagine.py",
    )
)
IMAGE_EDIT_PATH = Path(
    os.getenv(
        "GROK2API_IMAGE_EDIT_SERVICE_PATH",
        "/app/app/services/grok/services/image_edit.py",
    )
)
APP_CHAT_PATH = Path(
    os.getenv(
        "GROK2API_APP_CHAT_REVERSE_PATH",
        "/app/app/services/reverse/app_chat.py",
    )
)
CF_REFRESH_UTIL_PATH = Path(
    os.getenv(
        "GROK2API_CF_REFRESH_UTIL_PATH",
        "/app/app/services/reverse/utils/cf_refresh.py",
    )
)


CF_REFRESH_UTIL_MARKER = "Patched by grok-register: on-demand CF refresh helper"
CF_REFRESH_UTIL_CONTENT = """\"\"\"On-demand CF clearance refresh triggered by 403 responses.\"\"\"

import asyncio
import time

from loguru import logger

# Patched by grok-register: on-demand CF refresh helper
_last_cf_refresh_time: float = 0.0
_cf_refresh_lock = asyncio.Lock()
_CF_REFRESH_COOLDOWN = 60.0


async def trigger_cf_refresh_on_403() -> bool:
    \"\"\"Trigger a CF clearance refresh on 403 (with debounce).\"\"\"
    global _last_cf_refresh_time
    now = time.monotonic()
    if now - _last_cf_refresh_time < _CF_REFRESH_COOLDOWN:
        logger.debug(\"CF refresh skipped (cooldown)\")
        return False
    async with _cf_refresh_lock:
        now = time.monotonic()
        if now - _last_cf_refresh_time < _CF_REFRESH_COOLDOWN:
            return False
        logger.info(\"403 detected, triggering on-demand CF refresh...\")
        try:
            from app.services.cf_refresh.scheduler import refresh_once

            success = await refresh_once()
            _last_cf_refresh_time = time.monotonic()
            return success
        except Exception as e:
            logger.error(\"On-demand CF refresh failed: {}\", e)
            _last_cf_refresh_time = time.monotonic()
            return False
"""


PATCHES = [
    PatchSpec(
        path=IMAGE_PATH,
        marker="Patched by grok-register: widen image token retry budget",
        label="widen image token retry budget",
        old="""        max_token_retries = int(get_config("retry.max_retry") or 3)""",
        new="""        # Patched by grok-register: widen image token retry budget.
        # Image generation is burstier than text chat, so short-lived 429s
        # should have more room to rotate across healthy tokens.
        max_token_retries = max(int(get_config("retry.max_retry") or 3), 8)""",
    ),
    PatchSpec(
        path=IMAGE_PATH,
        marker="Patched by grok-register: align app-chat image stream fallback",
        label="align app-chat image stream fallback",
        old="""                    try:
                        try:
                            result = await self._stream_app_chat(
                                token_mgr=token_mgr,
                                token=current_token,
                                model_info=model_info,
                                prompt=prompt,
                                n=n,
                                response_format=response_format,
                                enable_nsfw=enable_nsfw,
                                chat_format=chat_format,
                            )
                        except UpstreamException as app_chat_error:
                            if rate_limited(app_chat_error):
                                raise
                            logger.warning(
                                "App-chat image stream failed, falling back to ws_imagine: %s",
                                app_chat_error,
                            )
                            result = await self._stream_ws(
                                token_mgr=token_mgr,
                                token=current_token,
                                model_info=model_info,
                                prompt=prompt,
                                n=n,
                                response_format=response_format,
                                size=size,
                                aspect_ratio=aspect_ratio,
                                enable_nsfw=enable_nsfw,
                                chat_format=chat_format,
                            )
                        async for chunk in result.data:
                            yielded = True
                            yield chunk
                        return""",
        new="""                    try:
                        # Patched by grok-register: try the REST app-chat
                        # imageGen path first, and fall back to ws_imagine
                        # when app-chat errors or yields no final images.
                        try:
                            result = await self._stream_app_chat(
                                token_mgr=token_mgr,
                                token=current_token,
                                model_info=model_info,
                                prompt=prompt,
                                n=n,
                                response_format=response_format,
                                enable_nsfw=enable_nsfw,
                                chat_format=chat_format,
                            )
                            async for chunk in result.data:
                                yielded = True
                                yield chunk
                            if not yielded:
                                raise UpstreamException(
                                    "Image generation returned no streamed results",
                                    details={"error": "empty_stream", "path": "app_chat"},
                                )
                        except UpstreamException as app_chat_error:
                            if rate_limited(app_chat_error):
                                raise
                            logger.warning(
                                "App-chat image stream failed or returned no final images, "
                                "falling back to ws_imagine: %s",
                                app_chat_error,
                            )
                            result = await self._stream_ws(
                                token_mgr=token_mgr,
                                token=current_token,
                                model_info=model_info,
                                prompt=prompt,
                                n=n,
                                response_format=response_format,
                                size=size,
                                aspect_ratio=aspect_ratio,
                                enable_nsfw=enable_nsfw,
                                chat_format=chat_format,
                            )
                            async for chunk in result.data:
                                yielded = True
                                yield chunk
                        return""",
    ),
    PatchSpec(
        path=IMAGE_PATH,
        marker="Patched by grok-register: align app-chat image collect fallback",
        label="align app-chat image collect fallback",
        old="""            try:
                try:
                    return await self._collect_app_chat(
                        token_mgr=token_mgr,
                        token=current_token,
                        model_info=model_info,
                        prompt=prompt,
                        n=n,
                        response_format=response_format,
                        enable_nsfw=enable_nsfw,
                    )
                except UpstreamException as app_chat_error:
                    if rate_limited(app_chat_error):
                        raise
                    logger.warning(
                        "App-chat image collect failed, falling back to ws_imagine: %s",
                        app_chat_error,
                    )
                    return await self._collect_ws(
                        token_mgr=token_mgr,
                        token=current_token,
                        model_info=model_info,
                        tried_tokens=tried_tokens,
                        prompt=prompt,
                        n=n,
                        response_format=response_format,
                        aspect_ratio=aspect_ratio,
                        enable_nsfw=enable_nsfw,
                    )""",
        new="""            try:
                # Patched by grok-register: try the REST app-chat imageGen
                # path first, and fall back to ws_imagine when app-chat
                # errors or returns no final generated images.
                try:
                    return await self._collect_app_chat(
                        token_mgr=token_mgr,
                        token=current_token,
                        model_info=model_info,
                        prompt=prompt,
                        n=n,
                        response_format=response_format,
                        enable_nsfw=enable_nsfw,
                    )
                except UpstreamException as app_chat_error:
                    if rate_limited(app_chat_error):
                        raise
                    logger.warning(
                        "App-chat image collect failed or returned no final images, "
                        "falling back to ws_imagine: %s",
                        app_chat_error,
                    )
                    return await self._collect_ws(
                        token_mgr=token_mgr,
                        token=current_token,
                        model_info=model_info,
                        tried_tokens=tried_tokens,
                        prompt=prompt,
                        n=n,
                        response_format=response_format,
                        aspect_ratio=aspect_ratio,
                        enable_nsfw=enable_nsfw,
                    )""",
    ),
    PatchSpec(
        path=IMAGE_PATH,
        marker="Patched by grok-register: cool image token after rate limit even after partial output",
        label="cool down real image token on 429",
        old="""                    except UpstreamException as e:
                        last_error = e
                        if rate_limited(e):
                            if yielded:
                                raise
                            await token_mgr.mark_rate_limited(current_token)
                            logger.warning(
                                f"Token {current_token[:10]}... rate limited (429), "
                                f"trying next token (attempt {attempt + 1}/{max_token_retries})"
                            )
                            continue
                        raise""",
        new="""                    except UpstreamException as e:
                        last_error = e
                        if rate_limited(e):
                            # Patched by grok-register: cool image token after rate limit even after partial output
                            try:
                                await token_mgr.mark_rate_limited(current_token)
                            except Exception as mark_error:
                                logger.warning(
                                    "Failed to cool down image token after 429: %s",
                                    mark_error,
                                )
                            if yielded:
                                raise
                            logger.warning(
                                f"Token {current_token[:10]}... rate limited (429), "
                                f"trying next token (attempt {attempt + 1}/{max_token_retries})"
                            )
                            continue
                        raise""",
    ),
    PatchSpec(
        path=IMAGE_PATH,
        marker="Patched by grok-register: use app-chat imageGen override for image stream",
        label="use app-chat imageGen override for image stream",
        old="""        response = await GrokChatService().chat(
            token=token,
            message=prompt,
            model=model_info.grok_model,
            mode=model_info.model_mode,
            stream=True,
            tool_overrides={"imageGen": True},
            request_overrides=self._app_chat_request_overrides(n, enable_nsfw),
        )""",
        new="""        # Patched by grok-register: use the REST app-chat
        # imageGen path with explicit model/mode, matching Jincheng's fix.
        response = await GrokChatService().chat(
            token=token,
            message=prompt,
            model=model_info.grok_model,
            mode=model_info.model_mode,
            stream=True,
            tool_overrides={"imageGen": True},
            request_overrides=self._app_chat_request_overrides(n, enable_nsfw),
        )""",
    ),
    PatchSpec(
        path=IMAGE_PATH,
        marker="Patched by grok-register: use app-chat imageGen override for image collect",
        label="use app-chat imageGen override for image collect",
        old="""        async def _call_generate(call_target: int) -> List[str]:
            response = await GrokChatService().chat(
                token=token,
                message=prompt,
                model=model_info.grok_model,
                mode=model_info.model_mode,
                stream=True,
                tool_overrides={"imageGen": True},
                request_overrides=self._app_chat_request_overrides(
                    call_target, enable_nsfw
                ),
            )""",
        new="""        async def _call_generate(call_target: int) -> List[str]:
            # Patched by grok-register: use the REST app-chat imageGen
            # path with explicit model/mode, matching Jincheng's fix.
            response = await GrokChatService().chat(
                token=token,
                message=prompt,
                model=model_info.grok_model,
                mode=model_info.model_mode,
                stream=True,
                tool_overrides={"imageGen": True},
                request_overrides=self._app_chat_request_overrides(
                    call_target, enable_nsfw
                ),
            )""",
    ),
    PatchSpec(
        path=IMAGE_EDIT_PATH,
        marker="Patched by grok-register: use modeId auto for image edit stream",
        label="use modeId auto for image edit stream",
        old="""                file_attachments = await self._upload_images(images, current_token)
                tool_overrides: Dict[str, Any] | None = None
                request_overrides = self._build_request_overrides(n)

                if stream:
                    response = await GrokChatService().chat(
                        token=current_token,
                        message=prompt,
                        model=_EDIT_UPSTREAM_MODEL,
                        mode=_EDIT_UPSTREAM_MODE,
                        stream=True,
                        file_attachments=file_attachments,
                        tool_overrides=tool_overrides,
                        request_overrides=request_overrides,
                    )""",
        new="""                file_attachments = await self._upload_images(images, current_token)
                tool_overrides: Dict[str, Any] = {
                    "gmailSearch": False,
                    "googleCalendarSearch": False,
                    "outlookSearch": False,
                    "outlookCalendarSearch": False,
                    "googleDriveSearch": False,
                }
                request_overrides = self._build_request_overrides(n)
                # Patched by grok-register: align image edit requests with
                # Grok's current modeId=auto routing and editing session flags.
                request_overrides["modeId"] = "auto"
                request_overrides["disableMemory"] = False
                request_overrides["temporary"] = False

                if stream:
                    response = await GrokChatService().chat(
                        token=current_token,
                        message=prompt,
                        model=None,
                        mode=None,
                        stream=True,
                        file_attachments=file_attachments,
                        tool_overrides=tool_overrides,
                        request_overrides=request_overrides,
                    )""",
    ),
    PatchSpec(
        path=IMAGE_EDIT_PATH,
        marker="Patched by grok-register: use modeId auto for image edit collect",
        label="use modeId auto for image edit collect",
        old="""        async def _call_edit():
            response = await GrokChatService().chat(
                token=token,
                message=prompt,
                model=_EDIT_UPSTREAM_MODEL,
                mode=_EDIT_UPSTREAM_MODE,
                stream=True,
                file_attachments=file_attachments,
                tool_overrides=tool_overrides,
                request_overrides=self._build_request_overrides(per_call),
            )""",
        new="""        async def _call_edit():
            edit_overrides = self._build_request_overrides(per_call)
            # Patched by grok-register: align image edit requests with
            # Grok's current modeId=auto routing and editing session flags.
            edit_overrides["modeId"] = "auto"
            edit_overrides["disableMemory"] = False
            edit_overrides["temporary"] = False
            response = await GrokChatService().chat(
                token=token,
                message=prompt,
                model=None,
                mode=None,
                stream=True,
                file_attachments=file_attachments,
                tool_overrides=tool_overrides,
                request_overrides=edit_overrides,
            )""",
    ),
    PatchSpec(
        path=IMAGE_EDIT_PATH,
        marker="Patched by grok-register: support cardAttachment image edit stream output",
        label="support cardAttachment image edit stream output",
        old="""                # modelResponse
                if mr := resp.get("modelResponse"):
                    if urls := _collect_images(mr):
                        for url in urls:
                            if self.response_format == "url":
                                processed = await self.process_url(url, "image")
                                if processed:
                                    final_images.append(processed)
                                continue
                            try:
                                dl_service = self._get_dl()
                                base64_data = await dl_service.parse_b64(
                                    url, self.token, "image"
                                )
                                if base64_data:
                                    if "," in base64_data:
                                        b64 = base64_data.split(",", 1)[1]
                                    else:
                                        b64 = base64_data
                                    final_images.append(b64)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to convert image to base64, falling back to URL: {e}"
                                )
                                processed = await self.process_url(url, "image")
                                if processed:
                                    final_images.append(processed)
                    continue""",
        new="""                # Patched by grok-register: support cardAttachment
                # image edit output from Grok's current response format.
                if ca := resp.get("cardAttachment"):
                    try:
                        jd = orjson.loads(ca.get("jsonData", b"{}"))
                        card_type = jd.get("type", "")
                        url = None
                        # Keep only final generated/edited image cards here.
                        # render_searched_image is an intermediate search layer,
                        # not the model's final output.
                        if card_type in ("render_generated_image", "render_edited_image"):
                            chunk = jd.get("image_chunk", {})
                            if chunk.get("progress", 0) >= 100 and chunk.get("imageUrl"):
                                url = f"https://assets.grok.com/{chunk['imageUrl']}"
                        if url:
                            if self.response_format == "url":
                                processed = await self.process_url(url, "image")
                                if processed:
                                    final_images.append(processed)
                            else:
                                try:
                                    dl_service = self._get_dl()
                                    base64_data = await dl_service.parse_b64(
                                        url, self.token, "image"
                                    )
                                    if base64_data:
                                        b64 = base64_data.split(",", 1)[1] if "," in base64_data else base64_data
                                        final_images.append(b64)
                                except Exception as e:
                                    logger.warning(f"Failed to convert stream card image to base64: {e}")
                                    processed = await self.process_url(url, "image")
                                    if processed:
                                        final_images.append(processed)
                    except Exception:
                        pass

                # modelResponse (legacy format)
                if mr := resp.get("modelResponse"):
                    if urls := _collect_images(mr):
                        for url in urls:
                            if self.response_format == "url":
                                processed = await self.process_url(url, "image")
                                if processed:
                                    final_images.append(processed)
                                continue
                            try:
                                dl_service = self._get_dl()
                                base64_data = await dl_service.parse_b64(
                                    url, self.token, "image"
                                )
                                if base64_data:
                                    if "," in base64_data:
                                        b64 = base64_data.split(",", 1)[1]
                                    else:
                                        b64 = base64_data
                                    final_images.append(b64)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to convert image to base64, falling back to URL: {e}"
                                )
                                processed = await self.process_url(url, "image")
                                if processed:
                                    final_images.append(processed)
                    continue""",
    ),
    PatchSpec(
        path=IMAGE_EDIT_PATH,
        marker="Patched by grok-register: support cardAttachment image edit collect output",
        label="support cardAttachment image edit collect output",
        old="""                if mr := resp.get("modelResponse"):
                    if urls := _collect_images(mr):
                        for url in urls:
                            if self.response_format == "url":
                                processed = await self.process_url(url, "image")
                                if processed:
                                    images.append(processed)
                                continue
                            try:
                                dl_service = self._get_dl()
                                base64_data = await dl_service.parse_b64(
                                    url, self.token, "image"
                                )
                                if base64_data:
                                    if "," in base64_data:
                                        b64 = base64_data.split(",", 1)[1]
                                    else:
                                        b64 = base64_data
                                    images.append(b64)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to convert image to base64, falling back to URL: {e}"
                                )
                                processed = await self.process_url(url, "image")
                                if processed:
                                    images.append(processed)""",
        new="""                # Patched by grok-register: support cardAttachment
                # image edit output from Grok's current response format.
                if ca := resp.get("cardAttachment"):
                    try:
                        jd = orjson.loads(ca.get("jsonData", b"{}"))
                        card_type = jd.get("type", "")
                        url = None
                        # Keep only final generated/edited image cards here.
                        # render_searched_image is an intermediate search layer,
                        # not the model's final output.
                        if card_type in ("render_generated_image", "render_edited_image"):
                            chunk = jd.get("image_chunk", {})
                            if chunk.get("progress", 0) >= 100 and chunk.get("imageUrl"):
                                url = f"https://assets.grok.com/{chunk['imageUrl']}"
                        if url:
                            if self.response_format == "url":
                                processed = await self.process_url(url, "image")
                                if processed:
                                    images.append(processed)
                            else:
                                try:
                                    dl_service = self._get_dl()
                                    base64_data = await dl_service.parse_b64(
                                        url, self.token, "image"
                                    )
                                    if base64_data:
                                        b64 = base64_data.split(",", 1)[1] if "," in base64_data else base64_data
                                        images.append(b64)
                                except Exception as e:
                                    logger.warning(f"Failed to convert card image to base64: {e}")
                                    processed = await self.process_url(url, "image")
                                    if processed:
                                        images.append(processed)
                    except Exception as card_err:
                        logger.warning(f"cardAttachment processing error: {card_err}")

                if mr := resp.get("modelResponse"):
                    if urls := _collect_images(mr):
                        for url in urls:
                            if self.response_format == "url":
                                processed = await self.process_url(url, "image")
                                if processed:
                                    images.append(processed)
                                continue
                            try:
                                dl_service = self._get_dl()
                                base64_data = await dl_service.parse_b64(
                                    url, self.token, "image"
                                )
                                if base64_data:
                                    if "," in base64_data:
                                        b64 = base64_data.split(",", 1)[1]
                                    else:
                                        b64 = base64_data
                                    images.append(b64)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to convert image to base64, falling back to URL: {e}"
                                )
                                processed = await self.process_url(url, "image")
                                if processed:
                                    images.append(processed)""",
    ),
    PatchSpec(
        path=APP_CHAT_PATH,
        marker="Patched by grok-register: allow modeId auto app-chat payloads",
        label="allow modeId auto app-chat payloads",
        old="""        payload = {
            "deviceEnvInfo": {
                "darkModeEnabled": False,
                "devicePixelRatio": 2,
                "screenHeight": 1329,
                "screenWidth": 2056,
                "viewportHeight": 1083,
                "viewportWidth": 2056,
            },
            "disableMemory": get_config("app.disable_memory"),
            "disableSearch": False,
            "disableSelfHarmShortCircuit": False,
            "disableTextFollowUps": False,
            "enableImageGeneration": True,
            "enableImageStreaming": True,
            "enableSideBySide": True,
            "fileAttachments": attachments,
            "forceConcise": False,
            "forceSideBySide": False,
            "imageAttachments": [],
            "imageGenerationCount": 2,
            "isAsyncChat": False,
            "isReasoning": False,
            "message": message,
            "modelMode": mode,
            "modelName": model,
            "responseMetadata": {
                "requestModelDetails": {"modelId": model},
            },
            "returnImageBytes": False,
            "returnRawGrokInXaiRequest": False,
            "sendFinalMetadata": True,
            "temporary": get_config("app.temporary"),
            "toolOverrides": tool_overrides or {},
        }
""",
        new="""        payload = {
            "deviceEnvInfo": {
                "darkModeEnabled": False,
                "devicePixelRatio": 2,
                "screenHeight": 1329,
                "screenWidth": 2056,
                "viewportHeight": 1083,
                "viewportWidth": 2056,
            },
            "disableMemory": get_config("app.disable_memory"),
            "disableSearch": False,
            "disableSelfHarmShortCircuit": False,
            "disableTextFollowUps": False,
            "enableImageGeneration": True,
            "enableImageStreaming": True,
            "enableSideBySide": True,
            "fileAttachments": attachments,
            "forceConcise": False,
            "forceSideBySide": False,
            "imageAttachments": [],
            "imageGenerationCount": 2,
            "isAsyncChat": False,
            "isReasoning": False,
            "message": message,
            "returnImageBytes": False,
            "returnRawGrokInXaiRequest": False,
            "sendFinalMetadata": True,
            "temporary": get_config("app.temporary"),
            "toolOverrides": tool_overrides or {},
        }

        # Patched by grok-register: allow modeId=auto app-chat payloads,
        # which aligns image requests with Grok's current website behavior.
        if model:
            payload["modelName"] = model
            payload["modelMode"] = mode
            payload["responseMetadata"] = {
                "requestModelDetails": {"modelId": model},
            }
        else:
            payload["modeId"] = "auto"
            payload["responseMetadata"] = {}
""",
    ),
    PatchSpec(
        path=APP_CHAT_PATH,
        marker="Patched by grok-register: trigger on-demand CF refresh on app-chat 403",
        label="trigger on-demand CF refresh on app-chat 403",
        old="""            async def _on_retry(attempt: int, status_code: int, error: Exception, delay: float):
                if active_proxy_key and should_rotate_proxy(status_code):
                    rotate_proxy(active_proxy_key)
""",
        new="""            async def _on_retry(attempt: int, status_code: int, error: Exception, delay: float):
                if active_proxy_key and should_rotate_proxy(status_code):
                    rotate_proxy(active_proxy_key)
                if status_code == 403:
                    # Patched by grok-register: trigger an on-demand
                    # Cloudflare refresh when app-chat gets blocked.
                    from app.services.reverse.utils.cf_refresh import trigger_cf_refresh_on_403 as _trigger_cf_refresh_on_403

                    await _trigger_cf_refresh_on_403()
""",
    ),
    PatchSpec(
        path=IMAGINE_PATH,
        marker="Patched by grok-register: retry imagine websocket stream on image 429",
        label="retry imagine websocket stream on 429",
        old="""            except Exception as e:
                logger.warning(f"Imagine stream error: {e}")
                await _send(
                    {
                        "type": "error",
                        "message": str(e),
                        "code": "internal_error",
                    }
                )
                await asyncio.sleep(1.5)""",
        new="""            except Exception as e:
                details = getattr(e, "details", {}) or {}
                if (
                    details.get("status") == 429
                    or details.get("error_code") == "rate_limit_exceeded"
                    or "rate limit exceeded" in str(e).lower()
                ):
                    # Patched by grok-register: retry imagine websocket stream on image 429
                    logger.warning(
                        f"Imagine stream rate limited, retrying with another token: {e}"
                    )
                    await asyncio.sleep(0.2)
                    continue
                logger.warning(f"Imagine stream error: {e}")
                await _send(
                    {
                        "type": "error",
                        "message": str(e),
                        "code": "internal_error",
                    }
                )
                await asyncio.sleep(1.5)""",
    ),
    PatchSpec(
        path=IMAGINE_PATH,
        marker="Patched by grok-register: retry imagine SSE stream on image 429",
        label="retry imagine sse stream on 429",
        old="""                except Exception as e:
                    logger.warning(f"Imagine SSE error: {e}")
                    yield (
                        f"data: {orjson.dumps({'type': 'error', 'message': str(e), 'code': 'internal_error'}).decode()}\\n\\n"
                    )
                    await asyncio.sleep(1.5)""",
        new="""                except Exception as e:
                    details = getattr(e, "details", {}) or {}
                    if (
                        details.get("status") == 429
                        or details.get("error_code") == "rate_limit_exceeded"
                        or "rate limit exceeded" in str(e).lower()
                    ):
                        # Patched by grok-register: retry imagine SSE stream on image 429
                        logger.warning(
                            f"Imagine SSE rate limited, retrying with another token: {e}"
                        )
                        await asyncio.sleep(0.2)
                        continue
                    logger.warning(f"Imagine SSE error: {e}")
                    yield (
                        f"data: {orjson.dumps({'type': 'error', 'message': str(e), 'code': 'internal_error'}).decode()}\\n\\n"
                    )
                    await asyncio.sleep(1.5)""",
    ),
]


def apply_patch(spec: PatchSpec) -> int:
    if not spec.path.exists():
        print(f"[patch] target not found for {spec.label}: {spec.path}", file=sys.stderr)
        return 1

    source = spec.path.read_text(encoding="utf-8")
    if spec.marker in source or spec.new in source:
        print(f"[patch] already applied ({spec.label}): {spec.path}")
        return 0

    if spec.old not in source:
        print(
            f"[patch] expected block not found for {spec.label}: {spec.path}",
            file=sys.stderr,
        )
        return 1

    updated = source.replace(spec.old, spec.new, 1)
    spec.path.write_text(updated, encoding="utf-8")
    print(f"[patch] applied {spec.label}: {spec.path}")
    return 0


def ensure_cf_refresh_helper() -> int:
    if CF_REFRESH_UTIL_PATH.exists():
        source = CF_REFRESH_UTIL_PATH.read_text(encoding="utf-8")
        if CF_REFRESH_UTIL_MARKER in source:
            print(f"[patch] already applied (cf refresh helper): {CF_REFRESH_UTIL_PATH}")
            return 0
        print(
            f"[patch] helper target already exists without marker: {CF_REFRESH_UTIL_PATH}",
            file=sys.stderr,
        )
        return 1

    CF_REFRESH_UTIL_PATH.parent.mkdir(parents=True, exist_ok=True)
    CF_REFRESH_UTIL_PATH.write_text(CF_REFRESH_UTIL_CONTENT, encoding="utf-8")
    print(f"[patch] created cf refresh helper: {CF_REFRESH_UTIL_PATH}")
    return 0


def main() -> int:
    status = 0
    for spec in PATCHES:
        status = max(status, apply_patch(spec))
    status = max(status, ensure_cf_refresh_helper())
    return status


if __name__ == "__main__":
    raise SystemExit(main())
