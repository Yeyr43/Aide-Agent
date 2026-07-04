"""web_fetch — 抓取 URL 内容并提取文本。

使用标准库 urllib，无外部依赖。自动处理编码、HTML 标签剥离。
安全限制：阻止内网/localhost IP、下载大小上限 5MB、内容上限 50000 字符。
"""

import ipaddress
import re
import socket
import urllib.request
import urllib.error
import ssl
from urllib.parse import urlparse

from core.locale import t

# ── 安全常量 ────────────────────────────────────────────────────────────

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024   # 5MB 下载硬上限
MAX_CHARS_HARD_LIMIT = 50_000           # 内容字符硬上限
MAX_TIMEOUT = 25                        # 超时上限（需在 fc_loop 的 30s 内）


def _is_private_host(host: str) -> bool:
    """检查主机是否为内网/本地地址（防 SSRF）。"""
    # 移除 IPv6 方括号
    host = host.strip("[]")

    # 本地地址
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return True

    # 尝试解析为 IP 地址
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 非 IP（域名）→ 需 DNS 解析
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except (socket.gaierror, OSError):
            return False  # 解析失败，放行让请求自己失败

    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast


async def execute(arguments: dict) -> str:
    """抓取 URL 并返回纯文本内容。

    Args:
        arguments: {"url": str, "timeout": int (可选, 默认 15), "max_chars": int (可选, 默认 30000)}

    Returns:
        URL 内容文本（HTML 标签已剥离）
    """
    url = arguments.get("url", "").strip()
    if not url:
        return t("tool.web_fetch.empty_url")

    if not url.startswith(("http://", "https://")):
        return t("tool.web_fetch.invalid_url")

    # ── URL 安全校验：阻止内网访问 ──
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return t("tool.web_fetch.invalid_url")
        if _is_private_host(host):
            return t("tool.web_fetch.private_host", host=host)
    except Exception:
        return t("tool.web_fetch.invalid_url")

    timeout = arguments.get("timeout", 15)
    if not isinstance(timeout, (int, float)) or timeout < 1:
        timeout = 15
    timeout = min(timeout, MAX_TIMEOUT)

    # ── 硬上限：max_chars 不允许超过 50000 ──
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
            },
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            # ── 分块下载，硬限制 5MB ──
            chunks: list[bytes] = []
            downloaded = 0
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    return t("tool.web_fetch.too_large", max_mb=MAX_DOWNLOAD_BYTES // (1024 * 1024))

            raw = b"".join(chunks)

            # 检测编码
            content_type = resp.headers.get("Content-Type", "")
            charset = _extract_charset(content_type)
            if not charset:
                charset = _detect_charset_from_html(raw)

            text = raw.decode(charset, errors="replace")

            # 提取纯文本
            text = _html_to_text(text)
            # 压缩连续空行
            text = re.sub(r"\n{3,}", "\n\n", text)
            # 限制长度
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n" + t("tool.web_fetch.truncated", n=len(text))

            return text

    except urllib.error.HTTPError as e:
        return t("tool.web_fetch.http_error", code=e.code, reason=e.reason)
    except urllib.error.URLError as e:
        return t("tool.web_fetch.unreachable", reason=e.reason)
    except ssl.SSLError as e:
        return t("tool.web_fetch.ssl_error", e=e)
    except TimeoutError:
        return t("tool.web_fetch.timeout", timeout=timeout)
    except Exception as e:
        return t("tool.web_fetch.failed", type=type(e).__name__, e=e)


def _extract_charset(content_type: str) -> str:
    """从 Content-Type 头提取 charset。"""
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    return m.group(1) if m else ""


def _detect_charset_from_html(raw: bytes) -> str:
    """从 HTML <meta charset> 标签检测编码。"""
    # 只检查前 4KB
    head = raw[:4096].decode("ascii", errors="ignore")
    m = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.IGNORECASE)
    return m.group(1) if m else "utf-8"


def _html_to_text(html: str) -> str:
    """将 HTML 转换为纯文本。"""
    # 移除 script/style 标签及其内容
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # 移除 HTML 注释
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # 将标题标签替换为 markdown 标题（必须在 block_tags 之前处理）
    html = re.sub(r"<h1\b[^>]*>", "\n# ", html, flags=re.IGNORECASE)
    html = re.sub(r"<h2\b[^>]*>", "\n## ", html, flags=re.IGNORECASE)
    html = re.sub(r"<h3\b[^>]*>", "\n### ", html, flags=re.IGNORECASE)
    # 将块级元素替换为换行（不含 h1-h6 避免覆盖 heading 转换）
    block_tags = (
        r"</?(?:div|p|li|tr|article|section|header|footer|aside|nav|main"
        r"|table|thead|tbody|tfoot|ul|ol|dl|dt|dd|pre|blockquote|figure|figcaption"
        r"|details|summary|fieldset|form|hr|br)\b[^>]*>"
    )
    html = re.sub(block_tags, "\n", html, flags=re.IGNORECASE)
    # 移除所有剩余标签
    html = re.sub(r"<[^>]+>", "", html)
    # 解码 HTML 实体
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    html = re.sub(r"&#x?[a-fA-F0-9]+;", " ", html)
    # 清理空白
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n +", "\n", html)
    return html.strip()


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "要抓取的 URL（必须以 http:// 或 https:// 开头）",
        },
        "timeout": {
            "type": "integer",
            "description": "请求超时秒数（默认 15，最大 60）",
        },
        "max_chars": {
            "type": "integer",
            "description": "返回内容最大字符数（默认 30000）",
        },
    },
    "required": ["url"],
}
