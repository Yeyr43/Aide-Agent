"""/language /api /model 命令 — 语言、API、模型管理。

P5: 从 handlers.py 拆分。
"""

import json
from pathlib import Path
from typing import Any

from core.locale import t
from core.setup import aide_dir
from ._compat import _cmd


@_cmd("language", t("cmd.language.desc"))
async def handle_language(app: Any, args: str) -> str:
    """切换界面语言。"""
    lang = args.strip().lower()
    if lang not in ("zh", "en"):
        if not lang:
            return t("cmd.language.usage")
        return t("cmd.language.unknown", lang=lang)

    from core.locale import set_locale
    set_locale(lang)

    # 持久化到 settings.json
    from core.config import Config
    settings = Config.load_settings()
    settings.setdefault("app", {})["locale"] = lang
    Config.save_settings(settings)

    # 刷新 UI 中的 locale 敏感字符串
    if app is not None and hasattr(app, '_cmd_handler'):
        input_box = app.query_one("#input", None)
        if input_box is not None:
            from core.locale import t as _t
            input_box.placeholder = _t("ui.widget.input_placeholder")
        # 重新注册命令（刷新描述）
        cmd_registry = getattr(app, '_cmd_registry', None)
        if cmd_registry is not None:
            from core.commands.builtin.handlers import register_builtin_commands
            register_builtin_commands(cmd_registry)
            from ui.textual_app.widgets.command_palette import CommandPalette
            palette = app.query_one("#palette", None)
            if palette is not None:
                palette.set_registry(cmd_registry)

    lang_display = {"zh": "中文", "en": "English"}.get(lang, lang)
    return t("cmd.language.switched", lang=lang_display)


@_cmd("api", t("cmd.api.desc"))
async def handle_api(app: Any, args: str) -> str:
    """管理 API Key 配置。无参数时打开配置页；list/delete 为文本命令。"""
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""

    settings_path = aide_dir() / "config" / "settings.json"

    # ── 无参数：打开 API 配置页 ──
    if not sub:
        from ui.textual_app.screens.api_config import ApiConfigScreen
        result = await app.push_screen_wait(ApiConfigScreen())
        if result is None:
            return ""  # 用户取消
        try:
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            else:
                settings = {}
        except (json.JSONDecodeError, OSError):
            settings = {}

        api_keys: dict = settings.get("api_keys", {})
        name = result["name"]
        api_keys[name] = {
            "provider": result["provider"],
            "model": result["model"],
            "api_key": result["api_key"],
            "base_url": result["base_url"],
            "supports_vision": result["supports_vision"],
        }
        settings["api_keys"] = api_keys
        if not settings.get("active_api"):
            settings["active_api"] = name
        if "app" not in settings:
            settings["app"] = {}
        ctx_raw = result.get("context_window", "128000")
        try:
            settings["app"]["context_window"] = int(ctx_raw) if ctx_raw else 128000
        except ValueError:
            pass
        _save_settings_dict(settings)
        return t("ui.api.saved", name=name, provider=result["provider"],
                 model=result["model"])

    # ── list ──
    if sub == "list":
        try:
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            else:
                settings = {}
        except (json.JSONDecodeError, OSError):
            settings = {}
        api_keys = settings.get("api_keys", {})
        if not api_keys:
            return t("cmd.api.list_empty")
        active = settings.get("active_api", "")
        lines = [t("cmd.api.list_title")]
        for n, cfg in api_keys.items():
            marker = f" {t('cmd.api.active')}" if n == active else ""
            lines.append(f"- **{n}**{marker} — {cfg.get('provider','?')}/{cfg.get('model','?')}")
        return "\n".join(lines)

    # ── delete ──
    if sub == "delete":
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            return t("cmd.api.delete_usage")
        try:
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            else:
                settings = {}
        except (json.JSONDecodeError, OSError):
            settings = {}
        api_keys = settings.get("api_keys", {})
        if name not in api_keys:
            return t("cmd.api.not_found", name=name)
        del api_keys[name]
        if settings.get("active_api") == name:
            settings["active_api"] = ""
        settings["api_keys"] = api_keys
        _save_settings_dict(settings)
        return t("cmd.api.deleted", name=name)

    # ── edit <name> ──
    if sub == "edit":
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            try:
                if settings_path.exists():
                    settings = json.loads(settings_path.read_text(encoding="utf-8"))
                else:
                    settings = {}
            except (json.JSONDecodeError, OSError):
                settings = {}
            name = settings.get("active_api", "")
        try:
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            else:
                settings = {}
        except (json.JSONDecodeError, OSError):
            settings = {}
        api_keys = settings.get("api_keys", {})
        if name not in api_keys:
            return t("cmd.api.not_found", name=name)
        from ui.textual_app.screens.api_config import ApiConfigScreen
        result = await app.push_screen_wait(ApiConfigScreen(edit_name=name))
        if result is None:
            return ""
        api_keys[result["name"]] = {
            "provider": result["provider"],
            "model": result["model"],
            "api_key": result["api_key"],
            "base_url": result["base_url"],
            "supports_vision": result["supports_vision"],
        }
        if result["name"] != name:
            del api_keys[name]
            if settings.get("active_api") == name:
                settings["active_api"] = result["name"]
        settings["api_keys"] = api_keys
        _save_settings_dict(settings)
        return t("ui.api.saved", name=result["name"], provider=result["provider"],
                 model=result["model"])

    return t("cmd.api.list_empty")


@_cmd("model", t("cmd.model.desc"))
async def handle_model(app: Any, args: str) -> str:
    """切换 / 管理 API 配置。"""
    parts = args.strip().split()
    name = parts[0] if parts else ""
    action = parts[1] if len(parts) > 1 else ""

    settings_path = aide_dir() / "config" / "settings.json"
    try:
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        else:
            settings = {}
    except (json.JSONDecodeError, OSError):
        settings = {}

    api_keys: dict = settings.get("api_keys", {})

    if not name:
        if not api_keys:
            return t("cmd.model.none")
        active = settings.get("active_api", "")
        lines = [t("cmd.api.list_title")]
        for n, cfg in api_keys.items():
            marker = f" {t('cmd.api.active')}" if n == active else ""
            lines.append(f"- **{n}**{marker} — {cfg.get('provider','?')}/{cfg.get('model','?')}")
        lines.append("")
        lines.append(t("cmd.model.usage"))
        return "\n".join(lines)

    if action == "delete":
        if name not in api_keys:
            return t("cmd.api.not_found", name=name)
        del api_keys[name]
        if settings.get("active_api") == name:
            settings["active_api"] = ""
        settings["api_keys"] = api_keys
        _save_settings_dict(settings)
        return t("cmd.api.deleted", name=name)

    if name not in api_keys:
        return t("cmd.api.not_found", name=name)

    cfg = api_keys[name]
    settings["active_api"] = name
    settings["llm"] = {
        "provider": cfg.get("provider", ""),
        "model": cfg.get("model", ""),
        "api_key": cfg.get("api_key", ""),
        "base_url": cfg.get("base_url", ""),
        "supports_vision": cfg.get("supports_vision", False),
    }
    settings["api_keys"] = api_keys
    _save_settings_dict(settings)

    if app is not None and hasattr(app, 'provider'):
        from core.config import Config
        from core.llm_gateway import create_provider
        config = Config.load()
        try:
            app.provider = create_provider(config.llm)
            app._model_name = config.llm.model or config.llm.provider
            app._kernel.set_provider(app.provider)
            from ui.textual_app.widgets.status_bar import StatusBar
            bar = app.query_one("#status-bar", StatusBar)
            bar.update_info(model=app._model_name, api_name=name)
        except Exception as e:
            return t("cmd.model.switched", name=name,
                     provider=cfg.get('provider', ''), model=cfg.get('model', '')) \
                   + f"\n⚠️ Provider 初始化失败: {e}"

    return t("cmd.model.switched", name=name,
             provider=cfg.get('provider', ''), model=cfg.get('model', ''))


def _save_settings_dict(settings: dict) -> None:
    """原子写入 settings.json。"""
    from core.config import Config
    Config.save_settings(settings)
