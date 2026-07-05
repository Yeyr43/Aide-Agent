"""冷启动引导 — 4 步向导。

页序：语言 → 模型配置（API）→ 角色模板 → 个性化与偏好。
CSS 拆分至 onboarding_css.py，页面构建器拆分至 onboarding_pages.py。
冷启动时禁用所有快捷键。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Static, Input, TextArea, Button

from core.locale import t, set_locale, build_soul
from core.setup import aide_dir

from .onboarding_css import ONBOARDING_CSS
from .onboarding_pages import (
    render_language_page,
    render_model_page,
    render_role_page,
    render_personal_page,
)

logger = logging.getLogger(__name__)

AGENT_ROOT = aide_dir() / "agent"
DATA_DIR = AGENT_ROOT / "data"
CONFIG_DIR = aide_dir() / "config"

# ── 页码常量 ────────────────────────────────────────────────────────

PAGE_LANGUAGE = 0
PAGE_MODEL = 1
PAGE_ROLE = 2
PAGE_PERSONAL = 3
TOTAL_PAGES = 4

# ── 角色模板 ─────────────────────────────────────────────────────────

ROLE_TEMPLATES = {
    "developer": {
        "personality": "专注代码，技术导向，简洁直接",
        "preferences": "分析需求后直接写代码，优先使用实际工具验证。给出可运行的方案而不是理论讨论。",
    },
    "writer": {
        "personality": "注重文字质量和表达，考虑受众感受",
        "preferences": "先讨论结构和风格再动笔。提供多个表达方案供选择，注重语言的准确性和美感。",
    },
    "manager": {
        "personality": "关注整体进度和决策记录，有条理",
        "preferences": "先梳理任务清单再逐项推进。记录关键决策，定期总结进度和待办事项。",
    },
}

# ── 动态字段 ID（用于清除） ────────────────────────────────────────────

_FIELD_IDS = [
    "lang-row", "btn-lang-zh", "btn-lang-en",
    "field-apiname", "field-hint-apiname",
    "field-name", "field-personality",
    "field-provider", "field-model", "field-apikey",
    "field-baseurl", "field-context-window", "field-vision-toggle",
    "field-workstyle", "field-longterm",
    "field-hint-name", "field-hint-personality",
    "field-hint-provider", "field-hint-model",
    "field-hint-apikey", "field-hint-baseurl", "field-hint-context-window",
    "field-hint-workstyle", "field-hint-longterm",
    "field-hint-newline-1",
    "role-row", "btn-role-developer", "btn-role-writer", "btn-role-manager", "btn-role-skip",
    "field-hint-role",
]


class OnboardingScreen(Screen):
    """冷启动引导 — 4 步向导。

    第 0 页：语言选择（中文 / English）
    第 1 页：模型配置（协议、模型、API Key、Base URL、多模态）
    第 2 页：角色模板（developer / writer / manager）
    第 3 页：个性化与偏好（称呼 + 个性 + 工作方式 + 长记忆）

    Enter 跳到下一页，Ctrl+J / Ctrl+Enter 在文本框中换行。
    """

    CSS = ONBOARDING_CSS
    BINDINGS = []  # 冷启动期间禁用所有快捷键

    def __init__(self) -> None:
        super().__init__()
        self._page = 0
        self._locale: str = "zh"
        self._answers: dict[str, str] = {}
        self._model_cfg: dict[str, str | bool] = {}

    # ── 组合 ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="onboard-container"):
            yield Static("", id="onboard-title")
            # 三栏导航行
            with Horizontal(id="onboard-nav"):
                with Container(id="nav-prev-area"):
                    yield Button(t("ui.onboard.nav_prev"), id="nav-prev")
                with Container(id="nav-page-area"):
                    yield Static("", id="nav-page")
                with Container(id="nav-next-area"):
                    yield Button(t("ui.onboard.nav_next"), id="nav-next")
            # 页码指示器（导航下方）
            yield Static("", id="onboard-page-indicator")

    def on_mount(self) -> None:
        self._render_page()

    # ── 安全查询辅助 ──────────────────────────────────────────────────

    def _safe_value(self, widget_id: str, widget_type, attr: str = "value") -> str | None:
        """安全读取 widget 值。widget 不存在时返回 None 并记录 debug 日志。"""
        import logging
        try:
            w = self.query_one(widget_id, widget_type)
            return getattr(w, attr) if attr != "text" else w.text
        except Exception:
            logging.getLogger(__name__).debug(
                "onboarding: widget %s (%s) not found, skipping", widget_id, widget_type.__name__)
            return None

    # ── 清除 + 挂载 ────────────────────────────────────────────────────

    def _clear_fields(self) -> None:
        for kid in _FIELD_IDS:
            try:
                w = self.query_one(f"#{kid}")
                w.remove()
            except Exception:
                logger.debug("Failed to remove widget %s in _clear_fields, skipping", kid)

    def _mount_before_nav(self, *widgets) -> None:
        anchor = self.query_one("#onboard-nav", Horizontal)
        for w in widgets:
            self.query_one("#onboard-container", Container).mount(w, before=anchor)

    def _render_page(self) -> None:
        self._clear_fields()

        title = self.query_one("#onboard-title", Static)
        prev_btn = self.query_one("#nav-prev", Button)
        nav_page = self.query_one("#nav-page", Static)
        next_btn = self.query_one("#nav-next", Button)
        indicator = self.query_one("#onboard-page-indicator", Static)

        nav_page.update(f"{self._page + 1}/{TOTAL_PAGES}")
        indicator.update("")

        # 语言选择页：隐藏导航；其他页：正常显示
        if self._page == PAGE_LANGUAGE:
            prev_btn.display = False
            next_btn.display = False
            nav_page.display = False
            indicator.display = False
        else:
            prev_btn.display = self._page > 0
            next_btn.display = True
            nav_page.display = True
            indicator.display = True
            if self._page == TOTAL_PAGES - 1:
                next_btn.label = t("ui.onboard.nav_done")
            else:
                next_btn.label = t("ui.onboard.nav_next")

        # 挂载当前页字段（委托给页面构建器）
        if self._page == PAGE_LANGUAGE:
            render_language_page(self, title)
        elif self._page == PAGE_MODEL:
            render_model_page(self, title)
        elif self._page == PAGE_ROLE:
            render_role_page(self, title)
        elif self._page == PAGE_PERSONAL:
            render_personal_page(self, title)

        # 聚焦第一个输入框
        inputs = self.query_one("#onboard-container", Container).query(Input)
        if inputs:
            inputs.first().focus()

    # ── 语言选择事件 ──────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-lang-zh")
    def _on_lang_zh(self) -> None:
        self._locale = "zh"
        set_locale("zh")
        self._go_next()

    @on(Button.Pressed, "#btn-lang-en")
    def _on_lang_en(self) -> None:
        self._locale = "en"
        set_locale("en")
        self._go_next()

    # ── 角色模板事件 ──────────────────────────────────────────────────

    def _apply_role(self, role_key: str) -> None:
        """应用角色模板到个性化字段。"""
        tmpl = ROLE_TEMPLATES.get(role_key)
        if tmpl:
            self._answers["personality"] = tmpl["personality"]
            self._answers["preferences"] = tmpl["preferences"]
        self._go_next()

    @on(Button.Pressed, "#btn-role-developer")
    def _on_role_developer(self) -> None:
        self._apply_role("developer")

    @on(Button.Pressed, "#btn-role-writer")
    def _on_role_writer(self) -> None:
        self._apply_role("writer")

    @on(Button.Pressed, "#btn-role-manager")
    def _on_role_manager(self) -> None:
        self._apply_role("manager")

    @on(Button.Pressed, "#btn-role-skip")
    def _on_role_skip(self) -> None:
        self._go_next()

    # ── 保存 ──────────────────────────────────────────────────────────

    def _save_current(self) -> None:
        if self._page == PAGE_MODEL:
            for key, wid in [("apiname", "#field-apiname"),
                              ("provider", "#field-provider"),
                              ("model", "#field-model"),
                              ("api_key", "#field-apikey"),
                              ("base_url", "#field-baseurl")]:
                val = self._safe_value(wid, Input)
                if val is not None:
                    self._model_cfg[key] = val

            # 多模态 toggle
            try:
                btn = self.query_one("#field-vision-toggle", Button)
                self._model_cfg["supports_vision"] = (
                    btn.label == t("ui.onboard.vision_on")
                )
            except Exception:
                logger.debug("Failed to query vision toggle button, skipping")

            # 上下文窗口
            val = self._safe_value("#field-context-window", Input)
            if val is not None:
                self._model_cfg["context_window"] = val

        elif self._page == PAGE_PERSONAL:
            val = self._safe_value("#field-name", Input)
            if val is not None:
                self._answers["name"] = val
            val = self._safe_value("#field-personality", Input)
            if val is not None:
                self._answers["personality"] = val
            for key, wid in [("preferences", "#field-workstyle"),
                              ("long_term", "#field-longterm")]:
                val = self._safe_value(wid, TextArea, attr="text")
                if val is not None:
                    self._answers[key] = val

    # ── 导航 ──────────────────────────────────────────────────────────

    def _go_next(self) -> None:
        self._save_current()
        if self._page >= TOTAL_PAGES - 1:
            self._finish()
        else:
            self._page += 1
            self._render_page()

    def _go_prev(self) -> None:
        self._save_current()
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def action_go_home(self) -> None:
        """阻止 Esc 跳过冷启动引导。"""
        pass

    # ── 按钮事件 ──────────────────────────────────────────────────────

    @on(Button.Pressed, "#nav-prev")
    def _on_nav_prev(self) -> None:
        self._go_prev()

    @on(Button.Pressed, "#nav-next")
    def _on_nav_next(self) -> None:
        self._go_next()

    @on(Button.Pressed, "#field-vision-toggle")
    def _on_vision_toggle(self) -> None:
        current = self._model_cfg.get("supports_vision", False)
        new_val = not current
        self._model_cfg["supports_vision"] = new_val
        try:
            btn = self.query_one("#field-vision-toggle", Button)
            btn.label = t("ui.onboard.vision_on") if new_val else t("ui.onboard.vision_off")
        except Exception:
            logger.debug("Failed to query vision toggle button in _on_vision_toggle, skipping")

    # ── 键盘处理 ──────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            focused = self.focused
            if isinstance(focused, TextArea):
                event.prevent_default()
                event.stop()
            elif isinstance(focused, Button):
                return
            self._go_next()
            return

        if event.key in ("ctrl+j", "ctrl+enter"):
            focused = self.focused
            if isinstance(focused, TextArea):
                focused.insert("\n")
                event.prevent_default()
                event.stop()
            return

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._go_next()

    # ── 完成 ──────────────────────────────────────────────────────────

    def _finish(self) -> None:
        self._save_current()

        name = self._answers.get("name", "Aide")
        personality = self._answers.get("personality", t("ui.onboard.default_personality"))
        preferences = self._answers.get("preferences", t("ui.onboard.default_workstyle"))
        long_term = self._answers.get("long_term", "")

        # ── soul.md（从模板 + 用户输入生成）──
        soul_content = build_soul(name)

        AGENT_ROOT.mkdir(parents=True, exist_ok=True)
        (AGENT_ROOT / "soul.md").write_text(soul_content, encoding="utf-8")

        if preferences:
            (AGENT_ROOT / "preferences.md").write_text(
                f"# {t('mem.label_preferences')}\n\n{preferences}\n", encoding="utf-8"
            )

        if long_term:
            (AGENT_ROOT / "long_term_memory.md").write_text(
                f"# {t('mem.label_long_term_memory')}\n\n{long_term}\n", encoding="utf-8"
            )

        # ── settings.json ──
        provider = self._model_cfg.get("provider", "")
        model = self._model_cfg.get("model", "")
        api_key = self._model_cfg.get("api_key", "")
        base_url = self._model_cfg.get("base_url", "")
        supports_vision = self._model_cfg.get("supports_vision", False)

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        settings: dict = {}
        settings_path = CONFIG_DIR / "settings.json"
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.debug("Failed to read settings.json in _finish, using empty settings")

        # locale 始终保存
        if "app" not in settings:
            settings["app"] = {}
        settings["app"]["locale"] = self._locale

        if provider or model:
            settings["llm"] = {
                "provider": str(provider),
                "model": str(model),
                "api_key": str(api_key),
                "base_url": str(base_url),
                "supports_vision": bool(supports_vision),
            }

            # API 名称 → 保存为独立配置文件 config/api/<name>.json
            apiname = self._model_cfg.get("apiname", "")
            if apiname:
                from core.config import Config
                api_cfg = {
                    "provider": str(provider),
                    "model": str(model),
                    "api_key": str(api_key),
                    "base_url": str(base_url),
                    "supports_vision": bool(supports_vision),
                }
                Config.save_api_config(apiname, api_cfg)
                Config.set_active_api_name(apiname)

            # 上下文窗口
            ctx_raw = self._model_cfg.get("context_window", "128000")
            try:
                ctx_val = int(ctx_raw) if ctx_raw else 128000
            except ValueError:
                ctx_val = 128000
            if "app" not in settings:
                settings["app"] = {}
            settings["app"]["context_window"] = ctx_val

        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ── 条目目录初始化 ──
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        source = {"session_id": "onboarding", "turn": 0}

        if preferences:
            (DATA_DIR / "preferences.json").write_text(
                json.dumps([{
                    "content": preferences, "source": source,
                    "status": "integrated",
                    "created_at": now, "updated_at": now,
                }], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if long_term:
            (DATA_DIR / "long_term_memory.json").write_text(
                json.dumps([{
                    "content": long_term, "source": source,
                    "status": "integrated",
                    "created_at": now, "updated_at": now,
                }], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        (DATA_DIR / "workflows.json").write_text("[]", encoding="utf-8")

        self.dismiss()
