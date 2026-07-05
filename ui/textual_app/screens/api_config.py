"""API 配置页 — /api 命令触发的独立设置屏幕。"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Static, Input, Button

from core.config import Config
from core.locale import t
from .api_config_css import API_CONFIG_CSS


class ApiConfigScreen(Screen):
    """独立的 API 配置屏幕。

    用法:
        await app.push_screen_wait(ApiConfigScreen())
        await app.push_screen_wait(ApiConfigScreen(edit_name="my-api"))
    """

    CSS = API_CONFIG_CSS

    def __init__(self, edit_name: str = "") -> None:
        super().__init__()
        self._edit_name = edit_name
        self._supports_vision = False

        # 预填：优先从 API 配置文件加载，其次用当前活跃 LLM 配置
        self._prefill: dict[str, str] = {}
        if edit_name:
            api_cfg = Config.load_api_config(edit_name)
            if api_cfg:
                self._prefill = {**api_cfg, "apiname": edit_name}
                self._supports_vision = api_cfg.get("supports_vision", False)
        else:
            # 新建模式：用当前活跃 LLM 的默认值
            from core.config import Config as Cfg
            config = Cfg.load()
            self._prefill = {
                "apiname": Config.get_active_api_name(),
                "provider": config.llm.provider,
                "model": config.llm.model,
                "api_key": config.llm.api_key,
                "base_url": config.llm.base_url,
            }
            self._supports_vision = config.llm.supports_vision

    def compose(self) -> ComposeResult:
        with Container(id="api-config-container"):
            yield Static(t("ui.onboard.model_title"), id="api-config-title")
            # Error message (hidden by default)
            yield Static("", id="api-error")
            # API Name
            yield Static(t("ui.api.label_name"), classes="api-label", id="api-field-hint-name")
            yield Input(
                value=self._prefill.get("apiname", ""),
                placeholder="my-api",
                classes="api-input", id="api-field-name",
            )
            # Provider
            yield Static(t("ui.onboard.label_provider"), classes="api-label", id="api-field-hint-provider")
            yield Input(
                value=self._prefill.get("provider", ""),
                placeholder="openai / ollama",
                classes="api-input", id="api-field-provider",
            )
            # Model
            yield Static(t("ui.onboard.label_model"), classes="api-label", id="api-field-hint-model")
            yield Input(
                value=self._prefill.get("model", ""),
                placeholder="gpt-4o / claude-3-5-sonnet / ...",
                classes="api-input", id="api-field-model",
            )
            # API Key
            yield Static(t("ui.onboard.label_api_key"), classes="api-label", id="api-field-hint-apikey")
            yield Input(
                value=self._prefill.get("api_key", ""),
                placeholder="sk-...",
                classes="api-input", id="api-field-apikey",
            )
            # Base URL
            yield Static(t("ui.onboard.label_base_url"), classes="api-label", id="api-field-hint-baseurl")
            yield Input(
                value=self._prefill.get("base_url", ""),
                placeholder="https://api.openai.com/v1",
                classes="api-input", id="api-field-baseurl",
            )
            # Context Window
            yield Static(t("ui.onboard.label_context_window"), classes="api-label", id="api-field-hint-ctx")
            yield Input(
                value=self._prefill.get("context_window", "128000"),
                placeholder=t("ui.onboard.ctx_placeholder"),
                classes="api-input", id="api-field-ctx",
            )
            # Vision toggle
            label = t("ui.onboard.vision_on") if self._supports_vision else t("ui.onboard.vision_off")
            yield Button(label, id="api-vision-toggle")
            # Buttons
            yield Static(
                t("ui.api.hint_newline"),
                classes="api-hint", id="api-hint-newline",
            )
            with Horizontal(id="api-btn-row"):
                yield Button(t("ui.api.btn_save"), id="api-btn-save")
                yield Button(t("ui.api.btn_cancel"), id="api-btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#api-field-name", Input).focus()

    @on(Button.Pressed, "#api-vision-toggle")
    def _on_vision_toggle(self) -> None:
        self._supports_vision = not self._supports_vision
        btn = self.query_one("#api-vision-toggle", Button)
        btn.label = t("ui.onboard.vision_on") if self._supports_vision else t("ui.onboard.vision_off")

    @on(Button.Pressed, "#api-btn-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)

    def _show_error(self, msg: str) -> None:
        """显示内联错误提示。"""
        err = self.query_one("#api-error", Static)
        err.update(msg)
        err.add_class("visible")

    def _clear_error(self) -> None:
        """隐藏内联错误提示。"""
        err = self.query_one("#api-error", Static)
        err.update("")
        err.remove_class("visible")

    @on(Button.Pressed, "#api-btn-save")
    def _on_save(self) -> None:
        name = self.query_one("#api-field-name", Input).value.strip()
        provider = self.query_one("#api-field-provider", Input).value.strip()
        model = self.query_one("#api-field-model", Input).value.strip()

        # 必填校验
        if not name or not provider or not model:
            self._show_error(t("ui.api.error_required"))
            return

        # 重名校验（编辑模式：改名后与已有配置冲突）
        if name != self._edit_name and Config.api_config_exists(name):
            self._show_error(t("ui.api.error_duplicate", name=name))
            return

        self._clear_error()

        result = {
            "name": name,
            "provider": provider,
            "model": model,
            "api_key": self.query_one("#api-field-apikey", Input).value.strip(),
            "base_url": self.query_one("#api-field-baseurl", Input).value.strip(),
            "context_window": self.query_one("#api-field-ctx", Input).value.strip(),
            "supports_vision": self._supports_vision,
        }
        self.dismiss(result)

    @on(Input.Submitted)
    def _on_input_submitted(self) -> None:
        self._on_save()
