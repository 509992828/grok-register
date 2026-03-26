
from __future__ import annotations

import json
import random
import re
import string
import time
from email import policy
from email.parser import BytesParser
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# 临时邮箱配置（从 config.json 加载）
# ============================================================

_config_path = Path(__file__).parent / "config.json"
_conf: Dict[str, Any] = {}
if _config_path.exists():
    with _config_path.open("r", encoding="utf-8") as _f:
        _conf = json.load(_f)

TEMP_MAIL_API_BASE = str(
    _conf.get("temp_mail_api_base")
    or _conf.get("duckmail_api_base")
    or ""
)
TEMP_MAIL_ADMIN_PASSWORD = str(
    _conf.get("temp_mail_admin_password")
    or _conf.get("duckmail_api_key")
    or _conf.get("duckmail_bearer")
    or ""
)
TEMP_MAIL_DOMAIN = str(_conf.get("temp_mail_domain") or _conf.get("duckmail_domain") or "")
TEMP_MAIL_SITE_PASSWORD = str(_conf.get("temp_mail_site_password", ""))
PROXY = str(_conf.get("proxy", ""))
TEMP_MAIL_PROVIDER = str(_conf.get("temp_mail_provider") or "").strip().lower()

# ============================================================
# 适配层：为 DrissionPage_example.py 提供简单接口
# ============================================================

_temp_email_cache: Dict[str, str] = {}


def get_email_and_token() -> Tuple[Optional[str], Optional[str]]:
    """
    创建临时邮箱并返回 (email, mail_token)。
    供 DrissionPage_example.py 调用。
    """
    email, _password, mail_token = create_temp_email()
    if email and mail_token:
        _temp_email_cache[email] = mail_token
        return email, mail_token
    return None, None


def get_oai_code(dev_token: str, email: str, timeout: int = 30) -> Optional[str]:
    """
    轮询收件箱获取 OTP 验证码。
    供 DrissionPage_example.py 调用。

    Returns:
        验证码字符串（去除连字符，如 "MM0SF3"）或 None
    """
    code = wait_for_verification_code(mail_token=dev_token, timeout=timeout)
    if code:
        code = code.replace("-", "")
    return code


# ============================================================
# 临时邮箱核心函数
# ============================================================


def _detect_mail_provider(api_base: str) -> str:
    if TEMP_MAIL_PROVIDER in {"duckmail", "temp-mail", "temp_mail", "generic"}:
        return "duckmail" if TEMP_MAIL_PROVIDER == "duckmail" else "generic"
    hostname = (urlparse(api_base).hostname or "").lower()
    if "duckmail" in hostname:
        return "duckmail"
    return "generic"


def _provider_label() -> str:
    return "DuckMail" if _detect_mail_provider(TEMP_MAIL_API_BASE) == "duckmail" else "Temp Mail"

def _create_session():
    """创建请求会话（优先 curl_cffi）。"""
    if curl_requests:
        session = curl_requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if PROXY:
            session.proxies = {"http": PROXY, "https": PROXY}
        return session, True

    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    if PROXY:
        s.proxies = {"http": PROXY, "https": PROXY}
    return s, False


def _do_request(session, use_cffi, method, url, **kwargs):
    """统一请求，curl_cffi 自动附带 impersonate。"""
    if use_cffi:
        kwargs.setdefault("impersonate", "chrome131")
    return getattr(session, method)(url, **kwargs)


def _build_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if TEMP_MAIL_SITE_PASSWORD:
        headers["x-custom-auth"] = TEMP_MAIL_SITE_PASSWORD
    if extra:
        headers.update(extra)
    return headers


def _generate_local_part(length: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _generate_mail_password(length: int = 18) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _build_duckmail_headers(token: str = "") -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _extract_duckmail_token(payload: Dict[str, Any]) -> str:
    for key in ("token", "jwt", "access_token", "id_token"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _extract_duckmail_domain_name(item: Dict[str, Any]) -> str:
    for key in ("domain", "name", "address"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _resolve_duckmail_domain(session, use_cffi, api_base: str) -> str:
    if TEMP_MAIL_DOMAIN:
        return TEMP_MAIL_DOMAIN

    headers = _build_duckmail_headers(TEMP_MAIL_ADMIN_PASSWORD)
    res = _do_request(
        session,
        use_cffi,
        "get",
        f"{api_base}/domains",
        params={"page": 1},
        headers=headers,
        timeout=20,
    )
    if res.status_code != 200:
        raise Exception(f"获取 DuckMail 域名失败: {res.status_code} - {res.text[:200]}")

    data = res.json()
    if not isinstance(data, dict):
        raise Exception("DuckMail 域名接口返回格式异常")

    domains = data.get("hydra:member") or data.get("data") or data.get("results") or []
    if not isinstance(domains, list) or not domains:
        raise Exception("DuckMail 域名列表为空，请在配置里显式填写 temp_mail_domain")

    public_verified: List[str] = []
    verified: List[str] = []
    fallback: List[str] = []
    for item in domains:
        if not isinstance(item, dict):
            continue
        domain = _extract_duckmail_domain_name(item)
        if not domain:
            continue
        fallback.append(domain)
        if item.get("isVerified") is True:
            verified.append(domain)
            if item.get("isPublic") is True or item.get("ownerId") in (None, "", 0):
                public_verified.append(domain)

    for candidates in (public_verified, verified, fallback):
        if candidates:
            return candidates[0]
    raise Exception("DuckMail 域名列表里没有可用域名，请在配置里显式填写 temp_mail_domain")


def _create_duckmail_email() -> Tuple[str, str, str]:
    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    session, use_cffi = _create_session()
    domain = _resolve_duckmail_domain(session, use_cffi, api_base)
    create_headers = _build_duckmail_headers(TEMP_MAIL_ADMIN_PASSWORD)
    last_error = ""

    for _ in range(5):
        email_local = _generate_local_part(random.randint(8, 12))
        email = f"{email_local}@{domain}"
        password = _generate_mail_password()

        res = _do_request(
            session,
            use_cffi,
            "post",
            f"{api_base}/accounts",
            json={
                "address": email,
                "password": password,
                "expiresIn": 86400,
            },
            headers=create_headers,
            timeout=20,
        )
        if res.status_code in {200, 201}:
            auth_res = _do_request(
                session,
                use_cffi,
                "post",
                f"{api_base}/token",
                json={"address": email, "password": password},
                timeout=20,
            )
            if auth_res.status_code != 200:
                raise Exception(f"登录 DuckMail 邮箱失败: {auth_res.status_code} - {auth_res.text[:200]}")

            token_data = auth_res.json()
            if not isinstance(token_data, dict):
                raise Exception("DuckMail token 接口返回格式异常")

            mail_token = _extract_duckmail_token(token_data)
            if not mail_token:
                raise Exception(f"DuckMail token 接口未返回 token: {token_data}")

            print(f"[*] DuckMail 临时邮箱创建成功: {email}")
            return email, password, mail_token

        if res.status_code in {409, 422}:
            last_error = f"{res.status_code} - {res.text[:200]}"
            continue

        raise Exception(f"创建 DuckMail 邮箱失败: {res.status_code} - {res.text[:200]}")

    raise Exception(f"创建 DuckMail 邮箱失败，重试后仍冲突: {last_error}")


def create_temp_email() -> Tuple[str, str, str]:
    """创建临时邮箱地址，返回 (email, password, mail_token)。"""
    if not TEMP_MAIL_API_BASE:
        raise Exception("temp_mail_api_base 未设置，无法创建临时邮箱")

    provider = _detect_mail_provider(TEMP_MAIL_API_BASE)
    if provider == "duckmail":
        try:
            return _create_duckmail_email()
        except Exception as e:
            raise Exception(f"DuckMail 临时邮箱创建失败: {e}")

    if not TEMP_MAIL_ADMIN_PASSWORD:
        raise Exception("temp_mail_admin_password 未设置，无法创建临时邮箱")
    if not TEMP_MAIL_DOMAIN:
        raise Exception("temp_mail_domain 未设置，无法创建临时邮箱")

    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    email_local = _generate_local_part(random.randint(8, 12))
    session, use_cffi = _create_session()
    headers = _build_headers({"x-admin-auth": TEMP_MAIL_ADMIN_PASSWORD})

    try:
        res = _do_request(
            session,
            use_cffi,
            "post",
            f"{api_base}/admin/new_address",
            json={
                "name": email_local,
                "domain": TEMP_MAIL_DOMAIN,
                "enablePrefix": False,
            },
            headers=headers,
            timeout=20,
        )
        if res.status_code != 200:
            raise Exception(f"创建邮箱失败: {res.status_code} - {res.text[:200]}")

        data = res.json()
        email = data.get("address") or ""
        mail_token = data.get("jwt") or ""
        password = data.get("password") or ""
        if not email or not mail_token:
            raise Exception(f"接口返回缺少 address/jwt: {data}")

        print(f"[*] Temp Mail 临时邮箱创建成功: {email}")
        return email, password, mail_token
    except Exception as e:
        raise Exception(f"Temp Mail 临时邮箱创建失败: {e}")


def _fetch_duckmail_emails(mail_token: str) -> List[Dict[str, Any]]:
    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    headers = _build_duckmail_headers(mail_token)
    session, use_cffi = _create_session()
    res = _do_request(
        session,
        use_cffi,
        "get",
        f"{api_base}/messages",
        params={"page": 1},
        headers=headers,
        timeout=20,
    )
    if res.status_code != 200:
        return []
    data = res.json()
    if not isinstance(data, dict):
        return []
    return data.get("hydra:member") or data.get("data") or data.get("results") or data.get("messages") or []


def fetch_emails(mail_token: str) -> List[Dict[str, Any]]:
    """获取邮件列表。"""
    if _detect_mail_provider(TEMP_MAIL_API_BASE) == "duckmail":
        try:
            return _fetch_duckmail_emails(mail_token)
        except Exception:
            return []

    try:
        api_base = TEMP_MAIL_API_BASE.rstrip("/")
        headers = _build_headers({"Authorization": f"Bearer {mail_token}"})
        session, use_cffi = _create_session()
        res = _do_request(
            session,
            use_cffi,
            "get",
            f"{api_base}/api/mails",
            params={"limit": 20, "offset": 0},
            headers=headers,
            timeout=20,
        )
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                return data.get("results") or data.get("data") or []
    except Exception:
        pass
    return []


def _normalize_message_id(msg_id: Any) -> str:
    raw = str(msg_id or "").strip()
    if raw.startswith("/"):
        return raw.rsplit("/", 1)[-1]
    return raw


def _fetch_duckmail_email_detail(mail_token: str, msg_id: str) -> Optional[Dict[str, Any]]:
    api_base = TEMP_MAIL_API_BASE.rstrip("/")
    normalized_id = _normalize_message_id(msg_id)
    headers = _build_duckmail_headers(mail_token)
    session, use_cffi = _create_session()

    res = _do_request(
        session,
        use_cffi,
        "get",
        f"{api_base}/messages/{normalized_id}",
        headers=headers,
        timeout=20,
    )
    if res.status_code != 200:
        return None

    data = res.json()
    if not isinstance(data, dict):
        return None

    if not any(data.get(key) for key in ("text", "html", "raw", "source")):
        src_res = _do_request(
            session,
            use_cffi,
            "get",
            f"{api_base}/sources/{normalized_id}",
            headers=headers,
            timeout=20,
        )
        if src_res.status_code == 200:
            src_data = src_res.json()
            if isinstance(src_data, dict):
                raw_source = src_data.get("data") or src_data.get("source") or src_data.get("raw") or ""
                if raw_source:
                    data["raw"] = raw_source
    return data


def fetch_email_detail(mail_token: str, msg_id: str) -> Optional[Dict[str, Any]]:
    """获取单封邮件详情。"""
    if _detect_mail_provider(TEMP_MAIL_API_BASE) == "duckmail":
        try:
            return _fetch_duckmail_email_detail(mail_token, msg_id)
        except Exception:
            return None

    try:
        api_base = TEMP_MAIL_API_BASE.rstrip("/")
        headers = _build_headers({"Authorization": f"Bearer {mail_token}"})
        session, use_cffi = _create_session()
        res = _do_request(
            session,
            use_cffi,
            "get",
            f"{api_base}/api/mail/{msg_id}",
            headers=headers,
            timeout=20,
        )
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def wait_for_verification_code(mail_token: str, timeout: int = 120) -> Optional[str]:
    """轮询临时邮箱，等待验证码邮件。"""
    start = time.time()
    seen_ids = set()

    while time.time() - start < timeout:
        messages = fetch_emails(mail_token)
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

            detail = fetch_email_detail(mail_token, str(msg_id))
            if not detail:
                continue

            content = _extract_mail_content(detail)
            code = extract_verification_code(content)
            if code:
                print(f"[*] 从 {_provider_label()} 提取到验证码: {code}")
                return code
        time.sleep(3)
    return None


def _stringify_mail_part(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_stringify_mail_part(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _extract_mail_content(detail: Dict[str, Any]) -> str:
    """兼容 text/html/raw MIME 三种内容来源。"""
    direct_parts = [
        detail.get("subject"),
        detail.get("text"),
        detail.get("html"),
        detail.get("raw"),
        detail.get("source"),
    ]
    direct_content = "\n".join(_stringify_mail_part(part) for part in direct_parts if part)
    if detail.get("text") or detail.get("html"):
        return direct_content

    raw = detail.get("raw") or detail.get("source")
    if not raw or not isinstance(raw, str):
        return direct_content
    return f"{direct_content}\n{_parse_raw_email(raw)}"


def _parse_raw_email(raw: str) -> str:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw.encode("utf-8", errors="ignore"))
    except Exception:
        return raw

    parts: List[str] = []
    subject = message.get("subject")
    if subject:
        parts.append(f"Subject: {subject}")

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            content = _decode_email_part(part)
            if content:
                parts.append(content)
    else:
        content = _decode_email_part(message)
        if content:
            parts.append(content)
    return "\n".join(parts)


def _decode_email_part(part) -> str:
    try:
        content = part.get_content()
        if isinstance(content, bytes):
            charset = part.get_content_charset() or "utf-8"
            content = content.decode(charset, errors="ignore")
        if not isinstance(content, str):
            content = str(content)
        if "html" in (part.get_content_type() or "").lower():
            content = _html_to_text(content)
        return content.strip()
    except Exception:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="ignore").strip()
    return ""


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return unescape(re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def extract_verification_code(content: str) -> Optional[str]:
    """
    从邮件内容提取验证码。
    Grok/x.ai 格式：MM0-SF3（3位-3位字母数字混合）或 6 位纯数字。
    """
    if not content:
        return None

    # 模式 1: Grok 格式 XXX-XXX
    m = re.search(r"(?<![A-Z0-9-])([A-Z0-9]{3}-[A-Z0-9]{3})(?![A-Z0-9-])", content)
    if m:
        return m.group(1)

    # 模式 2: 带标签的验证码
    m = re.search(r"(?:verification code|验证码|your code)[:\s]*[<>\s]*([A-Z0-9]{3}-[A-Z0-9]{3})\b", content, re.IGNORECASE)
    if m:
        return m.group(1)

    # 模式 3: HTML 样式包裹
    m = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?([A-Z0-9]{3}-[A-Z0-9]{3})[\s\S]*?</p>", content)
    if m:
        return m.group(1)

    # 模式 4: Subject 行 6 位数字
    m = re.search(r"Subject:.*?(\d{6})", content)
    if m and m.group(1) != "177010":
        return m.group(1)

    # 模式 5: HTML 标签内 6 位数字
    for code in re.findall(r">\s*(\d{6})\s*<", content):
        if code != "177010":
            return code

    # 模式 6: 独立 6 位数字
    for code in re.findall(r"(?<![&#\d])(\d{6})(?![&#\d])", content):
        if code != "177010":
            return code

    return None
