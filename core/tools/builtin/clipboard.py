"""clipboard — 读写系统剪贴板。

通过 pyperclip 实现跨平台剪贴板访问。
"""

from core.locale import t


async def execute(arguments: dict) -> str:
    """读或写系统剪贴板。

    Args:
        arguments: {"action": str, "text": str (optional, for write)}

    Returns:
        操作结果字符串。action="read" 返回剪贴板文本内容。
    """
    action = arguments.get("action", "read").strip().lower()
    import pyperclip

    if action == "read":
        try:
            text = pyperclip.paste()
            if not text:
                return t("tool.clipboard.empty")
            # 截断过长内容
            if len(text) > 8000:
                text = text[:8000] + "\n" + t("tool.clipboard.truncated")
            return text
        except Exception as e:
            return t("tool.clipboard.read_failed", e=e)

    elif action == "write":
        text = arguments.get("text", "")
        if not text:
            return t("tool.clipboard.empty_text")
        try:
            pyperclip.copy(text)
            return t("tool.clipboard.written", n=len(text))
        except Exception as e:
            return t("tool.clipboard.write_failed", e=e)

    else:
        return t("tool.clipboard.unknown_action", action=action)


# ── JSON Schema ───────────────────────────────────────────────────

schema = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["read", "write"],
            "description": "剪贴板操作类型：read（读取剪贴板内容），write（写入文本到剪贴板）",
        },
        "text": {
            "type": "string",
            "description": "要写入剪贴板的文本（仅在 action=write 时需要）",
        },
    },
    "required": ["action"],
}
