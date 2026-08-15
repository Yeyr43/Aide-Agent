"""web — 联网搜索 + URL 抓取，二合一。

安全限制：搜索最多 10 条、15s 超时；抓取阻止内网 IP、5MB 下载上限、50000 字符上限。
"""

from __future__ import annotations

import asyncio
import html as _html_mod
import ipaddress
import re
import socket
import ssl
import urllib.request
import urllib.error
from urllib.parse import urlparse

from ddgs import DDGS

from core.locale import t
from .definition import ToolDefinition

# ── 搜索常量 ──────────────────────────────────────────────────────────────

_SEARCH_TIMEOUT = 15.0
_MAX_RESULTS = 10

# ── 抓取常量 ──────────────────────────────────────────────────────────────

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_CHARS_HARD_LIMIT = 50_000
MAX_TIMEOUT = 25


async def execute(arguments: dict) -> str:
    """web 工具 — 根据 action 分发到搜索或抓取。

    Args:
        arguments: {
            "action": "search" | "fetch",
            "query": str          — 搜索查询词（action=search 时需要）
            "url": str            — 要抓取的 URL（action=fetch 时需要）
            "num": int            — 搜索结果数（可选，默认 5，最大 10）
            "timeout": int        — 超时秒数（可选，默认 15）
            "max_chars": int      — 抓取最大字符数（可选，默认 30000）
        }
    """
    action = arguments.get("action", "").strip().lower()

    if action == "search":
        return await _do_search(arguments)
    elif action == "fetch":
        return await _do_fetch(arguments)
    else:
        return t("tool.web.unknown_action", action=action)


# ── 搜索 ──────────────────────────────────────────────────────────────────

async def _do_search(arguments: dict) -> str:
    query = arguments.get("query", "").strip()
    if not query:
        return t("tool.web.empty_query")

    num = arguments.get("num", 5)
    if not isinstance(num, int) or num < 1:
        num = 5
    num = min(num, _MAX_RESULTS)

    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_search, query, num),
            timeout=_SEARCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return t("tool.web.search_timeout", timeout=_SEARCH_TIMEOUT)
    except Exception as e:
        return t("tool.web.search_failed", e=e)

    if not results:
        return t("tool.web.no_results", query=query)

    lines = [t("tool.web.results_for", query=query) + "\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", t("tool.web.untitled"))
        url = r.get("href", r.get("url", ""))
        snippet = r.get("body", r.get("snippet", ""))[:200]
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}\n")

    return "\n".join(lines)


def _search(query: str, num: int) -> list[dict]:
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=num))


# ── 抓取 ──────────────────────────────────────────────────────────────────

def _is_private_host(host: str) -> bool:
    host = host.strip("[]")
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except (socket.gaierror, OSError):
            return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that validates each redirect target against _is_private_host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        host = parsed.hostname or ""
        if host and _is_private_host(host):
            raise urllib.error.URLError(
                f"redirect to private host blocked: {host}"
            )
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )


async def _do_fetch(arguments: dict) -> str:
    url = arguments.get("url", "").strip()
    if not url:
        return t("tool.web.empty_url")
    if not url.startswith(("http://", "https://")):
        return t("tool.web.invalid_url")

    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return t("tool.web.invalid_url")
        if _is_private_host(host):
            return t("tool.web.private_host", host=host)
    except Exception:
        return t("tool.web.invalid_url")

    timeout = arguments.get("timeout", 15)
    if not isinstance(timeout, (int, float)) or timeout < 1:
        timeout = 15
    timeout = min(timeout, MAX_TIMEOUT)

    max_chars = arguments.get("max_chars", 30000)
    if not isinstance(max_chars, int) or max_chars < 1:
        max_chars = 30000
    max_chars = min(max_chars, MAX_CHARS_HARD_LIMIT)

    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AideAgent/1.0 (local personal assistant)",
                "Accept": "text/html,text/plain,*/*",
                "Accept-Encoding": "identity",
            },
        )
        opener = urllib.request.build_opener(
            _SafeRedirectHandler(),
            urllib.request.HTTPSHandler(context=ctx),
        )
        with opener.open(req, timeout=timeout) as resp:
            # 设置 socket 级别超时（每次 read 都受此约束，防止慢速服务器挂起）
            sock = getattr(resp.fp, 'raw', None) or getattr(resp.fp, '_sock', None)
            if sock is not None:
                sock.settimeout(timeout)
            chunks: list[bytes] = []
            downloaded = 0
            while True:
                try:
                    chunk = resp.read(8192)
                except socket.timeout:
                    return t("tool.web.timeout", timeout=timeout)
                if not chunk:
                    break
                chunks.append(chunk)
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    return t("tool.web.too_large", max_mb=MAX_DOWNLOAD_BYTES // (1024 * 1024))

            raw = b"".join(chunks)

            content_type = resp.headers.get("Content-Type", "")
            if content_type and not _is_text_content(content_type):
                return t("tool.web.non_text_content", type=content_type.split(";")[0].strip())

            charset = _extract_charset(content_type)
            if not charset:
                charset = _detect_charset_from_html(raw)

            text = raw.decode(charset, errors="replace")
            text = _html_to_text(text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n" + t("tool.web.truncated", n=len(text))
            return text

    except urllib.error.HTTPError as e:
        return t("tool.web.http_error", code=e.code, reason=e.reason)
    except urllib.error.URLError as e:
        return t("tool.web.unreachable", reason=e.reason)
    except ssl.SSLError as e:
        return t("tool.web.ssl_error", e=e)
    except TimeoutError:
        return t("tool.web.timeout", timeout=timeout)
    except Exception as e:
        return t("tool.web.failed", type=type(e).__name__, e=e)


def _extract_charset(content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    return m.group(1) if m else ""


def _detect_charset_from_html(raw: bytes) -> str:
    head = raw[:4096].decode("ascii", errors="ignore")
    m = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.IGNORECASE)
    return m.group(1) if m else "utf-8"


def _is_text_content(content_type: str) -> bool:
    """Check if Content-Type indicates text/* content."""
    main_type = content_type.split(";")[0].strip().lower()
    return main_type.startswith("text/")


def _html_to_text(html: str) -> str:
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<h1\b[^>]*>", "\n# ", html, flags=re.IGNORECASE)
    html = re.sub(r"<h2\b[^>]*>", "\n## ", html, flags=re.IGNORECASE)
    html = re.sub(r"<h3\b[^>]*>", "\n### ", html, flags=re.IGNORECASE)
    block_tags = (
        r"</?(?:div|p|li|tr|article|section|header|footer|aside|nav|main"
        r"|table|thead|tbody|tfoot|ul|ol|dl|dt|dd|pre|blockquote|figure|figcaption"
        r"|details|summary|fieldset|form|hr|br)\b[^>]*>"
    )
    html = re.sub(block_tags, "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = _html_mod.unescape(html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n +", "\n", html)
    return html.strip()


# ── JSON Schema ───────────────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["search", "fetch"],
            "description": "操作类型：search（联网搜索）或 fetch（抓取 URL 内容）",
        },
        "query": {
            "type": "string",
            "description": "搜索查询词（action=search 时需要）",
        },
        "url": {
            "type": "string",
            "description": "要抓取的 URL（action=fetch 时需要，以 http:// 或 https:// 开头）",
        },
        "num": {
            "type": "integer",
            "description": "返回结果数量（action=search，默认 5，最大 10）",
        },
        "timeout": {
            "type": "integer",
            "description": "请求超时秒数（默认 15，最大 25）",
        },
        "max_chars": {
            "type": "integer",
            "description": "返回内容最大字符数（action=fetch，默认 30000，最大 50000）",
        },
    },
    "required": ["action"],
}


definition = ToolDefinition(
    name="web",
    description=t("tool_desc.web"),
    parameters=schema,
    execute=execute,
)
