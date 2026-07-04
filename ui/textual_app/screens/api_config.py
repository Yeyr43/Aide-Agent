"""API 配置页 — /api 命令触发的独立设置屏幕。"""

from __future__ import annotations

import json

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Static, Input, Button

from core.locale import t
from core.setup import aide_dir

CONFIG_DIR = aide_dir() / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.json"


class ApiConfigScreen(Screen):
    """独立的 API 配置屏幕。

    用法:
        await app.push_screen_wait(ApiConfigScreen())
    """

    CSS = """
    ApiConfigScreen {
        background: #0c0c0c;
        align: center middle;
    }

    #api-config-container {
        width: 56;
        height: auto;
        padding: 2 3;
    }

    #api-config-title {
        color: #c8c8c0;
        text-style: bold;
        margin-bottom: 2;
        text-align: center;
        width: 100%;
    }

    .api-label {
        color: #888888;
        margin-bottom: 1;
    }

    .api-input {
        width: 100%;
        margin-bottom: 1;
        background: #121212;
        color: #c8c8c0;
        border: solid #2a2a3a;
        padding: 0 1;
    }
    .api-input:focus {
        border: solid #7ec8e3;
    }

    #api-vision-toggle {
        border: none;
        background: transparent;
        color: #7ec8e3;
        min-width: 16;
        padding: 0 1;
        margin-bottom: 1;
    }
    #api-vision-toggle:hover {
        color: #c8c8c0;
    }

    #api-btn-row {
        width: 100%;
        height: auto;
        margin-top: 1;
        align: center middle;
    }

    #api-btn-row Button {
        margin: 0 1;
        min-width: 14;
    }
    #api-btn-save {
        background: #1a3a2a;
        border: solid #7ec8e3;
        color: #7ec8e3;
    }
    #api-btn-save:hover {
        background: #2a5a3a;
        color: #00d4ff;
    }
    #api-btn-cancel {
        background: transparent;
        border: solid #444444;
        color: #888888;
    }

    .api-hint {
        color: #555555;
        margin-bottom: 1;
    }
    """

    def __init__(self, edit_name: str = "") -> None:
        super().__init__()
        self._edit_name = edit_name
        self._supports_vision = False

        # 预填：如果编辑已有配置，加载其值；否则用当前 LLM 配置
        self._prefill: dict[str, str] = {}
        try:
            if SETTINGS_PATH.exists():
                settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                api_keys = settings.get("api_keys", {})
                if edit_name and edit_name in api_keys:
                    self._prefill = api_keys[edit_name]
                    self._prefill["apiname"] = edit_name
                else:
                    llm = settings.get("llm", {})
                    self._prefill = {
                        "apiname": settings.get("active_api", ""),
                        "provider": llm.get("provider", ""),
                        "model": llm.get("model", ""),
                        "api_key": llm.get("api_key", ""),
                        "base_url": llm.get("base_url", ""),
                    }
                    self._supports_vision = llm.get("supports_vision", False)
        except (json.JSONDecodeError, OSError):
            pass

    def compose(self) -> ComposeResult:
        with Container(id="api-config-container"):
            yield Static(t("ui.onboard.model_title"), id="api-config-title")
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

    @on(Button.Pressed, "#api-btn-save")
    def _on_save(self) -> None:
        result = {
            "name": self.query_one("#api-field-name", Input).value.strip(),
            "provider": self.query_one("#api-field-provider", Input).value.strip(),
            "model": self.query_one("#api-field-model", Input).value.strip(),
            "api_key": self.query_one("#api-field-apikey", Input).value.strip(),
            "base_url": self.query_one("#api-field-baseurl", Input).value.strip(),
            "context_window": self.query_one("#api-field-ctx", Input).value.strip(),
            "supports_vision": self._supports_vision,
        }
        if not result["name"] or not result["provider"] or not result["model"]:
            return  # need at minimum: name, provider, model

        self.dismiss(result)

    @on(Input.Submitted)
    def _on_input_submitted(self) -> None:
        self._on_save()
