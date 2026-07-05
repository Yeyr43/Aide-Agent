"""OnboardingScreen 页面构建器 — 从 onboarding.py 拆分。

每个页面渲染函数负责挂载该页的 widget。
"""

from __future__ import annotations

from textual.widgets import Static, Input, TextArea, Button
from textual.containers import Horizontal

from core.locale import t


def render_language_page(screen, title: Static) -> None:
    """第 0 页：语言选择。"""
    title.update(t("ui.onboard.lang_title"))

    # 隐藏导航按钮（语言选择页不需要）
    screen.query_one("#nav-prev", Button).display = False
    screen.query_one("#nav-next", Button).display = False
    screen.query_one("#nav-page", Static).display = False
    screen.query_one("#onboard-page-indicator", Static).display = False

    # 两个按钮并排一行，青蓝色高亮
    row = Horizontal(id="lang-row")
    screen._mount_before_nav(row)
    row.mount(
        Button(t("ui.onboard.lang_zh"), id="btn-lang-zh", classes="lang-btn"),
        Button(t("ui.onboard.lang_en"), id="btn-lang-en", classes="lang-btn"),
    )


def render_model_page(screen, title: Static) -> None:
    """第 1 页：模型配置。"""
    title.update(t("ui.onboard.model_title"))

    # API 名称
    screen._mount_before_nav(
        Static(t("ui.api.label_name"), classes="onboard-label", id="field-hint-apiname"),
    )
    screen._mount_before_nav(
        Input(value=str(screen._model_cfg.get("apiname", "")),
              placeholder="my-api",
              classes="onboard-input", id="field-apiname"),
    )

    fields = [
        ("field-hint-provider", t("ui.onboard.label_provider"), "field-provider",
         str(screen._model_cfg.get("provider", "")), "openai / ollama"),
        ("field-hint-model", t("ui.onboard.label_model"), "field-model",
         str(screen._model_cfg.get("model", "")), "gpt-4o / claude-3-5-sonnet / ..."),
        ("field-hint-apikey", t("ui.onboard.label_api_key"), "field-apikey",
         str(screen._model_cfg.get("api_key", "")), "sk-..."),
        ("field-hint-baseurl", t("ui.onboard.label_base_url"), "field-baseurl",
         str(screen._model_cfg.get("base_url", "")), "https://api.openai.com/v1"),
    ]

    for hint_id, hint_text, field_id, field_val, placeholder in fields:
        screen._mount_before_nav(
            Static(hint_text, classes="onboard-label", id=hint_id),
        )
        screen._mount_before_nav(
            Input(value=field_val, placeholder=placeholder,
                  classes="onboard-input", id=field_id),
        )

    # 上下文窗口
    ctx_val = str(screen._model_cfg.get("context_window", "128000"))
    screen._mount_before_nav(
        Static(t("ui.onboard.label_context_window"), classes="onboard-label",
               id="field-hint-context-window"),
    )
    screen._mount_before_nav(
        Input(value=ctx_val, placeholder=t("ui.onboard.ctx_placeholder"),
              classes="onboard-input", id="field-context-window"),
    )

    # 多模态 toggle
    vision_val = screen._model_cfg.get("supports_vision", False)
    label = t("ui.onboard.vision_on") if vision_val else t("ui.onboard.vision_off")
    screen._mount_before_nav(
        Button(label, id="field-vision-toggle"),
    )


def render_role_page(screen, title: Static) -> None:
    """第 2 页：角色模板选择 — 三个预设 + 跳过按钮。"""
    title.update(t("ui.onboard.role_title"))

    screen._mount_before_nav(
        Static(t("ui.onboard.role_desc"), classes="onboard-hint", id="field-hint-role"),
    )

    # 角色按钮（竖排）
    for role_key, role_label, role_desc_key in [
        ("developer", t("ui.onboard.role_dev_label"), t("ui.onboard.role_dev_desc")),
        ("writer", t("ui.onboard.role_writer_label"), t("ui.onboard.role_writer_desc")),
        ("manager", t("ui.onboard.role_mgr_label"), t("ui.onboard.role_mgr_desc")),
    ]:
        screen._mount_before_nav(
            Button(f"{role_label}\n{role_desc_key}",
                   id=f"btn-role-{role_key}", classes="role-btn"),
        )

    # 跳过按钮
    screen._mount_before_nav(
        Button(t("ui.onboard.role_skip"), id="btn-role-skip", classes="role-skip-btn"),
    )


def render_personal_page(screen, title: Static) -> None:
    """第 3 页：个性化与偏好。"""
    title.update(t("ui.onboard.personal_title"))

    screen._mount_before_nav(
        Static(t("ui.onboard.label_name"), classes="onboard-label", id="field-hint-name"),
    )
    screen._mount_before_nav(
        Input(value=screen._answers.get("name", "Aide"),
              classes="onboard-input", id="field-name"),
    )
    screen._mount_before_nav(
        Static(t("ui.onboard.label_personality"), classes="onboard-label",
               id="field-hint-personality"),
    )
    screen._mount_before_nav(
        Input(value=screen._answers.get("personality", t("ui.onboard.default_personality")),
              classes="onboard-input", id="field-personality"),
    )

    # 工作方式 + 长记忆
    screen._mount_before_nav(
        Static(t("ui.onboard.label_workstyle"), classes="onboard-label",
               id="field-hint-workstyle"),
    )
    screen._mount_before_nav(
        TextArea(screen._answers.get("preferences", t("ui.onboard.default_workstyle")),
                 classes="onboard-textarea", id="field-workstyle"),
    )
    screen._mount_before_nav(
        Static(t("ui.onboard.label_longterm"), classes="onboard-label",
               id="field-hint-longterm"),
    )
    screen._mount_before_nav(
        TextArea(screen._answers.get("long_term", ""),
                 classes="onboard-textarea", id="field-longterm"),
    )

    screen._mount_before_nav(
        Static(t("ui.onboard.hint_newline"),
               classes="onboard-hint", id="field-hint-newline-1"),
    )
