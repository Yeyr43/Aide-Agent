"""冷启动引导 — 3 步向导。

页序：语言 → 模型配置（API）→ 个性化与偏好。
单行无边框导航（< 上一页 · 1/3 · 下一页 >）。
Enter 前进，Ctrl+J / Ctrl+Enter 换行（TextArea）。
冷启动时禁用所有快捷键。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Static, Input, TextArea, Button

from core.locale import t, set_locale, build_soul
from core.setup import aide_dir

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
    "role-row", "btn-role-dev", "btn-role-writer", "btn-role-mgr", "btn-role-skip",
    "field-hint-role",
]


class OnboardingScreen(Screen):
    """冷启动引导 — 3 步向导。

    第 0 页：语言选择（中文 / English）
    第 1 页：模型配置（协议、模型、API Key、Base URL、多模态）
    第 2 页：个性化与偏好（称呼 + 个性 + 工作方式 + 长记忆）

    Enter 跳到下一页，Ctrl+J / Ctrl+Enter 在文本框中换行。
    """

    CSS = """
    OnboardingScreen {
        background: #0c0c0c;
        align: center middle;
    }

    #onboard-container {
        width: 56;
        height: auto;
        padding: 2 3;
    }

    #onboard-title {
        color: #c8c8c0;
        text-style: bold;
        margin-bottom: 2;
        text-align: center;
        width: 100%;
    }

    .onboard-label {
        color: #888888;
        margin-bottom: 1;
    }

    .onboard-input {
        width: 100%;
        margin-bottom: 1;
        background: #121212;
        color: #c8c8c0;
        border: solid #2a2a3a;
        padding: 0 1;
    }
    .onboard-input:focus {
        border: solid #7ec8e3;
    }

    .onboard-textarea {
        width: 100%;
        height: 4;
        margin-bottom: 1;
        background: #121212;
        color: #c8c8c0;
        border: solid #2a2a3a;
    }
    .onboard-textarea:focus {
        border: solid #7ec8e3;
    }

    .onboard-hint {
        color: #555555;
        margin-bottom: 1;
    }

    /* ── 底部导航：三栏同行 ── */
    #onboard-nav {
        width: 100%;
        height: auto;
        margin-top: 2;
    }

    #onboard-nav Container {
        height: auto;
    }

    #nav-prev-area {
        width: 1fr;
        content-align: left middle;
    }

    #nav-page-area {
        width: 1fr;
        content-align: center middle;
    }

    #nav-next-area {
        width: 1fr;
        content-align: right middle;
    }

    #onboard-nav Button {
        border: none;
        background: transparent;
        color: #7ec8e3;
        min-width: 10;
        padding: 0 1;
    }
    #onboard-nav Button:hover {
        color: #c8c8c0;
    }

    #nav-page {
        color: #555555;
    }

    #onboard-page-indicator {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: #444444;
    }

    #lang-row {
        width: 100%;
        height: auto;
        margin: 2 0;
        align: center middle;
    }

    .lang-btn {
        width: 1fr;
        margin: 0 1;
        padding: 1 0;
        border: solid #7ec8e3;
        background: #121212;
        color: #7ec8e3;
        text-style: bold;
        content-align: center middle;
    }
    .lang-btn:hover {
        border: solid #00d4ff;
        background: #1a2a3a;
        color: #00d4ff;
    }

    #field-vision-toggle {
        border: none;
        background: transparent;
        color: #7ec8e3;
        min-width: 16;
        padding: 0 1;
        margin-bottom: 1;
    }
    #field-vision-toggle:hover {
        color: #c8c8c0;
    }

    #role-row {
        width: 100%;
        height: auto;
        margin: 2 0;
        align: center middle;
    }

    .role-btn {
        width: 1fr;
        margin: 0 0 1 0;
        padding: 1 0;
        border: solid #7ec8e3;
        background: #121212;
        color: #7ec8e3;
        text-style: bold;
        content-align: center middle;
    }
    .role-btn:hover {
        border: solid #00d4ff;
        background: #1a2a3a;
        color: #00d4ff;
    }

    .role-skip-btn {
        width: 1fr;
        margin: 1 0 0 0;
        padding: 1 0;
        border: none;
        background: transparent;
        color: #555555;
        content-align: center middle;
    }
    .role-skip-btn:hover {
        color: #c8c8c0;
    }
    """

    BINDINGS = []  # 冷启动期间禁用所有快捷键

    def __init__(self) -> None:
        super().__init__()
        self._page = 0
        self._locale: str = "zh"
        self._answers: dict[str, str] = {}
        self._model_cfg: dict[str, str | bool] = {}

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

    # ── 清除 + 挂载 ────────────────────────────────────────────────────

    def _clear_fields(self) -> None:
        for kid in _FIELD_IDS:
            try:
                w = self.query_one(f"#{kid}")
                w.remove()
            except Exception:
                pass

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

        # 挂载当前页字段
        if self._page == PAGE_LANGUAGE:
            self._render_language(title)
        elif self._page == PAGE_MODEL:
            self._render_model(title)
        elif self._page == PAGE_ROLE:
            self._render_role(title)
        elif self._page == PAGE_PERSONAL:
            self._render_personal(title)

        # 聚焦第一个输入框（语言选择页没有 Input，跳过）
        inputs = self.query_one("#onboard-container", Container).query(Input)
        if inputs:
            inputs.first().focus()

    # ── 第 0 页：语言选择 ──────────────────────────────────────────────

    def _render_language(self, title: Static) -> None:
        title.update(t("ui.onboard.lang_title"))

        # 隐藏导航按钮（语言选择页不需要）
        self.query_one("#nav-prev", Button).display = False
        self.query_one("#nav-next", Button).display = False
        self.query_one("#nav-page", Static).display = False
        self.query_one("#onboard-page-indicator", Static).display = False

        # 两个按钮并排一行，青蓝色高亮
        row = Horizontal(id="lang-row")
        self._mount_before_nav(row)
        row.mount(
            Button(t("ui.onboard.lang_zh"), id="btn-lang-zh", classes="lang-btn"),
            Button(t("ui.onboard.lang_en"), id="btn-lang-en", classes="lang-btn"),
        )

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

    # ── 第 1 页：模型配置 ──────────────────────────────────────────────

    def _render_model(self, title: Static) -> None:
        title.update(t("ui.onboard.model_title"))

        # API 名称（新增）
        self._mount_before_nav(
            Static(t("ui.api.label_name"), classes="onboard-label", id="field-hint-apiname"),
        )
        self._mount_before_nav(
            Input(value=str(self._model_cfg.get("apiname", "")),
                  placeholder="my-api",
                  classes="onboard-input", id="field-apiname"),
        )

        fields = [
            ("field-hint-provider", t("ui.onboard.label_provider"), "field-provider",
             str(self._model_cfg.get("provider", "")), "openai / ollama"),
            ("field-hint-model", t("ui.onboard.label_model"), "field-model",
             str(self._model_cfg.get("model", "")), "gpt-4o / claude-3-5-sonnet / ..."),
            ("field-hint-apikey", t("ui.onboard.label_api_key"), "field-apikey",
             str(self._model_cfg.get("api_key", "")), "sk-..."),
            ("field-hint-baseurl", t("ui.onboard.label_base_url"), "field-baseurl",
             str(self._model_cfg.get("base_url", "")), "https://api.openai.com/v1"),
        ]

        for hint_id, hint_text, field_id, field_val, placeholder in fields:
            self._mount_before_nav(
                Static(hint_text, classes="onboard-label", id=hint_id),
            )
            self._mount_before_nav(
                Input(value=field_val, placeholder=placeholder,
                      classes="onboard-input", id=field_id),
            )

        # 上下文窗口
        ctx_val = str(self._model_cfg.get("context_window", "128000"))
        self._mount_before_nav(
            Static(t("ui.onboard.label_context_window"), classes="onboard-label",
                   id="field-hint-context-window"),
        )
        self._mount_before_nav(
            Input(value=ctx_val, placeholder=t("ui.onboard.ctx_placeholder"),
                  classes="onboard-input", id="field-context-window"),
        )

        # 多模态 toggle
        vision_val = self._model_cfg.get("supports_vision", False)
        label = t("ui.onboard.vision_on") if vision_val else t("ui.onboard.vision_off")
        self._mount_before_nav(
            Button(label, id="field-vision-toggle"),
        )

    # ── 第 2 页：角色模板 ──────────────────────────────────────────────

    def _render_role(self, title: Static) -> None:
        """角色模板选择页 — 三个预设 + 跳过按钮。"""
        title.update(t("ui.onboard.role_title"))

        self._mount_before_nav(
            Static(t("ui.onboard.role_desc"), classes="onboard-hint", id="field-hint-role"),
        )

        # 角色按钮（竖排）
        for role_key, role_label, role_desc_key in [
            ("developer", t("ui.onboard.role_dev_label"), t("ui.onboard.role_dev_desc")),
            ("writer", t("ui.onboard.role_writer_label"), t("ui.onboard.role_writer_desc")),
            ("manager", t("ui.onboard.role_mgr_label"), t("ui.onboard.role_mgr_desc")),
        ]:
            self._mount_before_nav(
                Button(f"{role_label}\n{role_desc_key}",
                       id=f"btn-role-{role_key}", classes="role-btn"),
            )

        # 跳过按钮
        self._mount_before_nav(
            Button(t("ui.onboard.role_skip"), id="btn-role-skip", classes="role-skip-btn"),
        )

    def _apply_role(self, role_key: str) -> None:
        """应用角色模板到个性化字段。"""
        tmpl = ROLE_TEMPLATES.get(role_key)
        if tmpl:
            self._answers["personality"] = tmpl["personality"]
            self._answers["preferences"] = tmpl["preferences"]
        self._go_next()

    @on(Button.Pressed, "#btn-role-dev")
    def _on_role_dev(self) -> None:
        self._apply_role("developer")

    @on(Button.Pressed, "#btn-role-writer")
    def _on_role_writer(self) -> None:
        self._apply_role("writer")

    @on(Button.Pressed, "#btn-role-mgr")
    def _on_role_mgr(self) -> None:
        self._apply_role("manager")

    @on(Button.Pressed, "#btn-role-skip")
    def _on_role_skip(self) -> None:
        self._go_next()

    # ── 第 3 页：个性化与偏好 ────────────────────────────────────────

    def _render_personal(self, title: Static) -> None:
        title.update(t("ui.onboard.personal_title"))

        self._mount_before_nav(
            Static(t("ui.onboard.label_name"), classes="onboard-label", id="field-hint-name"),
        )
        self._mount_before_nav(
            Input(value=self._answers.get("name", "Aide"),
                  classes="onboard-input", id="field-name"),
        )
        self._mount_before_nav(
            Static(t("ui.onboard.label_personality"), classes="onboard-label",
                   id="field-hint-personality"),
        )
        self._mount_before_nav(
            Input(value=self._answers.get("personality", t("ui.onboard.default_personality")),
                  classes="onboard-input", id="field-personality"),
        )

        # 工作方式 + 长记忆（合并自原第 3 页）
        self._mount_before_nav(
            Static(t("ui.onboard.label_workstyle"), classes="onboard-label",
                   id="field-hint-workstyle"),
        )
        self._mount_before_nav(
            TextArea(self._answers.get("preferences", t("ui.onboard.default_workstyle")),
                     classes="onboard-textarea", id="field-workstyle"),
        )
        self._mount_before_nav(
            Static(t("ui.onboard.label_longterm"), classes="onboard-label",
                   id="field-hint-longterm"),
        )
        self._mount_before_nav(
            TextArea(self._answers.get("long_term", ""),
                     classes="onboard-textarea", id="field-longterm"),
        )

        self._mount_before_nav(
            Static(t("ui.onboard.hint_newline"),
                   classes="onboard-hint", id="field-hint-newline-1"),
        )

    # ── 保存 ────────────────────────────────────────────────────────────

    def _save_current(self) -> None:
        if self._page == PAGE_MODEL:
            for key, wid in [("apiname", "#field-apiname"),
                              ("provider", "#field-provider"),
                              ("model", "#field-model"),
                              ("api_key", "#field-apikey"),
                              ("base_url", "#field-baseurl")]:
                try:
                    self._model_cfg[key] = self.query_one(wid, Input).value
                except Exception:
                    pass
            # 多模态 toggle
            try:
                btn = self.query_one("#field-vision-toggle", Button)
                self._model_cfg["supports_vision"] = (
                    btn.label == t("ui.onboard.vision_on")
                )
            except Exception:
                pass
            # 上下文窗口
            try:
                raw = self.query_one("#field-context-window", Input).value
                self._model_cfg["context_window"] = raw
            except Exception:
                pass

        elif self._page == PAGE_PERSONAL:
            try:
                self._answers["name"] = self.query_one("#field-name", Input).value
            except Exception:
                pass
            try:
                self._answers["personality"] = self.query_one(
                    "#field-personality", Input).value
            except Exception:
                pass
            for key, wid in [("preferences", "#field-workstyle"),
                              ("long_term", "#field-longterm")]:
                try:
                    self._answers[key] = self.query_one(wid, TextArea).text
                except Exception:
                    pass

    # ── 导航 ────────────────────────────────────────────────────────────

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

    # ── 按钮事件 ────────────────────────────────────────────────────────

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
            pass

    # ── 键盘处理 ────────────────────────────────────────────────────────

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

    # ── 完成 ────────────────────────────────────────────────────────────

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
                pass

        # locale 始终保存（不受模型配置影响）
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

            # API 名称 → 保存到 api_keys
            apiname = self._model_cfg.get("apiname", "")
            if apiname:
                if "api_keys" not in settings:
                    settings["api_keys"] = {}
                settings["api_keys"][apiname] = {
                    "provider": str(provider),
                    "model": str(model),
                    "api_key": str(api_key),
                    "base_url": str(base_url),
                    "supports_vision": bool(supports_vision),
                }
                settings["active_api"] = apiname

            # 上下文窗口
            ctx_raw = self._model_cfg.get("context_window", "128000")
            try:
                ctx_val = int(ctx_raw) if ctx_raw else 128000
            except ValueError:
                ctx_val = 128000
            if "app" not in settings:
                settings["app"] = {}
            settings["app"]["context_window"] = ctx_val

        # 始终写入 settings.json（locale 必须落盘，否则重启后丢失）
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
