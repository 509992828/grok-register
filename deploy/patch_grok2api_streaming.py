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


PATCHES = [
    PatchSpec(
        path=IMAGE_PATH,
        marker="Patched by grok-register: app-chat image streaming can stall",
        label="prefer ws image stream",
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
                        # Patched by grok-register: app-chat image streaming can stall
                        # without yielding image events, so prefer ws_imagine here.
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
    if spec.marker in source:
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


def main() -> int:
    status = 0
    for spec in PATCHES:
        status = max(status, apply_patch(spec))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
