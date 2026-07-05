"""/language /api /model 命令 — 语言、API、模型管理。

API 配置存储为 config/api/<name>.json，每个 API 一个文件。
"""

from typing import Any

from core.config import Config
from core.locale import t
from ._compat import _cmd


def _resolve_edit_name(name: str) -> str:
    """解析 edit 目标名：显式指定 → 活跃 API → 空。"""
    if name:
        return name
    return Config.get_active_api_name()


def _list_display() -> str:
    """生成 API 列表的展示文本。"""
    api_configs = Config.list_api_configs()
    active = Config.get_active_api_name()
    if not api_configs:
        return t("cmd.api.list_empty")
    lines = [t("cmd.api.list_title")]
    for n, cfg in api_configs.items():
        marker = f" {t('cmd.api.active')}" if n == active else ""
        lines.append(
            f"- **{n}**{marker} — {cfg.get('provider', '?')}"
            f"/{cfg.get('model', '?')}"
        )
    return "\n".join(lines)


def _api_result_to_config(result: dict) -> dict:
    """将 ApiConfigScreen 返回的 dict 转为 API 配置 dict（仅存储字段）。"""
    return {
        "provider": result["provider"],
        "model": result["model"],
        "api_key": result["api_key"],
        "base_url": result["base_url"],
        "supports_vision": result["supports_vision"],
    }


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
            app.refresh_command_palette()

    lang_display = {"zh": "中文", "en": "English"}.get(lang, lang)
    return t("cmd.language.switched", lang=lang_display)


@_cmd("api", t("cmd.api.desc"))
async def handle_api(app: Any, args: str) -> str:
    """管理 API 配置。无参数打开配置页；list/delete/edit 为文本命令。"""
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""

    # ── 无参数：打开 API 配置页 ──
    if not sub:
        result = await app.open_api_config_screen()
        if result is None:
            return ""  # 用户取消

        name = result["name"]
        api_cfg = _api_result_to_config(result)
        Config.save_api_config(name, api_cfg)

        # 如果还没有活跃 API，自动设为当前
        if not Config.get_active_api_name():
            Config.set_active_api_name(name)

        # 持久化 context_window 到 settings.json
        settings = Config.load_settings()
        settings.setdefault("app", {})["context_window"] = _parse_ctx(result)
        Config.save_settings(settings)

        return t("ui.api.saved", name=name, provider=api_cfg["provider"],
                 model=api_cfg["model"])

    # ── list ──
    if sub == "list":
        return _list_display()

    # ── delete ──
    if sub == "delete":
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            return t("cmd.api.delete_usage")
        if not Config.api_config_exists(name):
            return t("cmd.api.not_found", name=name)
        Config.delete_api_config(name)
        # 如果删除的是活跃 API，清除 active_api
        if Config.get_active_api_name() == name:
            settings = Config.load_settings()
            settings["active_api"] = ""
            Config.save_settings(settings)
        return t("cmd.api.deleted", name=name)

    # ── edit <name> ──
    if sub == "edit":
        name = _resolve_edit_name(parts[1] if len(parts) > 1 else "")
        if not name or not Config.api_config_exists(name):
            return t("cmd.api.not_found", name=name) if name else t("cmd.api.list_empty")

        result = await app.open_api_config_screen(edit_name=name)
        if result is None:
            return ""

        new_name = result["name"]
        api_cfg = _api_result_to_config(result)
        Config.save_api_config(new_name, api_cfg)

        # 如果改名了，删除旧文件 + 更新 active_api
        if new_name != name:
            Config.delete_api_config(name)
            if Config.get_active_api_name() == name:
                Config.set_active_api_name(new_name)

        return t("ui.api.saved", name=new_name, provider=api_cfg["provider"],
                 model=api_cfg["model"])

    return t("cmd.api.list_empty")


@_cmd("model", t("cmd.model.desc"))
async def handle_model(app: Any, args: str) -> str:
    """切换 / 列出 API 配置。"""
    parts = args.strip().split()
    name = parts[0] if parts else ""
    action = parts[1] if len(parts) > 1 else ""

    api_configs = Config.list_api_configs()

    # ── 无参数：显示当前 API + 所有 API 列表 ──
    if not name:
        if not api_configs:
            return t("cmd.model.none")
        return _list_display() + "\n\n" + t("cmd.model.usage")

    # ── delete ──
    if action == "delete":
        if name not in api_configs:
            return t("cmd.api.not_found", name=name)
        Config.delete_api_config(name)
        if Config.get_active_api_name() == name:
            settings = Config.load_settings()
            settings["active_api"] = ""
            Config.save_settings(settings)
        return t("cmd.api.deleted", name=name)

    # ── 切换到指定 API ──
    if name not in api_configs:
        return t("cmd.api.not_found", name=name)

    cfg = api_configs[name]
    Config.set_active_api_name(name)

    # 更新运行时 provider
    if app is not None and hasattr(app, 'provider'):
        from core.config import Config as Cfg
        from core.llm_gateway import create_provider
        config = Cfg.load()
        try:
            app.provider = create_provider(config.llm)
            app._model_name = config.llm.model or config.llm.provider
            app._kernel.set_provider(app.provider)
            app._api_name = name
            app.refresh_status_bar_model()
        except Exception as e:
            return t("cmd.model.switched", name=name,
                     provider=cfg.get('provider', ''), model=cfg.get('model', '')) \
                   + f"\n⚠️ Provider 初始化失败: {e}"

    return t("cmd.model.switched", name=name,
             provider=cfg.get('provider', ''), model=cfg.get('model', ''))


def _parse_ctx(result: dict) -> int:
    """从 ApiConfigScreen 返回的 dict 中解析 context_window。"""
    ctx_raw = result.get("context_window", "128000")
    try:
        return int(ctx_raw) if ctx_raw else 128000
    except (ValueError, TypeError):
        return 128000
