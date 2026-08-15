"""Aide P4 — 前端。

纯暗色 TUI (PowerShell 黑 #0c0c0c)。
启动 → HomeScreen → 选择/创建会话 → 对话页。
AgentKernel 编排 LLM/session/context，Textual 只管 UI。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from core.setup import is_cold_start, has_existing_config
from core.locale import t, set_locale
from core.kernel import AppBootstrap
from core.llm_gateway.content_builder import build_user_content
from core.llm_gateway.image_utils import save_images_to_session
from core.kernel.protocols import TokenUsage
from core.sessions.restorer import restore_session_full

from .widgets.input_box import InputBox
from .widgets.message_list import MessageList
from .widgets.command_palette import CommandPalette
from .widgets.status_bar import StatusBar
from .screens.onboarding import OnboardingScreen
from .screens.home import HomeScreen, SessionSelected, NewSessionRequested
from .bridge import UIBridge
from .session_context import SessionContext
from .command_handler import CommandHandler


class AideApp(App):
    """Aide Agent P3 — 前端。"""

    TITLE = "Aide Agent"

    BINDINGS = [
        ("escape", "go_home", t("app.return_home")),
        ("ctrl+q", "noop", "Ctrl+Q disabled"),
    ]

    CSS_PATH = "app.tcss"

    def compose(self) -> ComposeResult:
        yield Static("", id="session-label")
        yield MessageList(id="messages")
        with Vertical(id="bottom-area"):
            yield CommandPalette(id="palette")
            yield InputBox(placeholder=t("ui.widget.input_placeholder"), id="input")
        yield StatusBar(id="status-bar")

    # ── 启动 ─────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """启动：委托 AppBootstrap 构建组件 → 冷启动检查 → 首页。"""
        msg_list = self.query_one("#messages", MessageList)

        # ── 组合根：Bootstrap 构建所有组件 ──
        try:
            result = await AppBootstrap.init()
        except FileNotFoundError as e:
            msg_list.add_error(str(e))
            return
        except Exception as e:
            msg_list.add_error(t("app.bootstrap_failed", e=e))
            return

        self._config = result.config
        set_locale(self._config.app.locale)
        self.provider = result.provider
        self._model_name = result.model_name
        self._api_name = result.config.app.active_api
        self._store = result.store
        self._tool_registry = result.tool_registry
        self._mcp_adapter = result.mcp_adapter
        self._cmd_registry = result.cmd_registry
        self._ingester = result.ingester
        self._pipeline = result.pipeline
        self._kernel = result.kernel

        # ── UI 层特有的初始化 ──
        self.query_one("#palette", CommandPalette).set_registry(self._cmd_registry)
        # compose() 在 on_mount 之前执行，需要刷新 locale 敏感的字符串
        self.query_one("#input", InputBox)._placeholder = t("ui.widget.input_placeholder")
        self._bridge = UIBridge(self)
        self._cmd_handler = CommandHandler(self)

        # ── 对话状态 ──
        self._session = SessionContext()
        self._restored_turns: list[dict] = []  # 已有会话恢复用的按轮记录
        self._last_usage: TokenUsage | None = None  # 来自 ChatResult 的 token 用量

        # ── 状态栏 + 冷启动引导 ──
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_info(model=self._model_name, api_name=self._api_name)

        self._startup_worker()

    @work(exclusive=True, thread=False)
    async def _startup_worker(self) -> None:
        """启动 worker：智能跳过已有配置 → 冷启动检查 → 引导 → 首页。"""
        if has_existing_config():
            self.push_screen(HomeScreen())
        elif is_cold_start():
            await self.push_screen_wait(OnboardingScreen())
            self._reload_after_onboarding()
            self.push_screen(HomeScreen())
        else:
            self.push_screen(HomeScreen())

    @work(exclusive=True, thread=False)
    async def _dispatch_command(self, cmd_def, args, msg_list, input_box, text) -> None:
        """在 worker 中执行命令，使 handler 可以使用 push_screen_wait。"""
        await self._cmd_handler.run_command(cmd_def, args, msg_list, input_box, text)

    def _reload_after_onboarding(self) -> None:
        """冷启动完成后重新加载配置和 provider。

        AppBootstrap.init() 在 OnboardingScreen 之前执行，那时
        settings.json 还不存在，provider 为 None。向导写入配置后
        需要重新加载，否则 provider 永远是空壳。
        """
        from core.config import Config
        from core.kernel import AppBootstrap
        from .widgets.status_bar import StatusBar

        config = Config.load()
        self._config = config
        set_locale(config.app.locale)

        try:
            self.provider, self._model_name = AppBootstrap.reload_provider(
                config, kernel=self._kernel, pipeline=self._pipeline,
            )
        except Exception as e:
            logger.warning(t("app.provider_init_failed", e=e))

        self._api_name = config.app.active_api
        # 更新状态栏
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_info(model=self._model_name, api_name=self._api_name)

    # ── 命令提示 ──────────────────────────────────────────────────────

    def on_input_box_command_input(self, event: InputBox.CommandInput) -> None:
        """输入 / 时显示/过滤命令面板。"""
        palette = self.query_one("#palette", CommandPalette)
        if event.text.startswith("/"):
            palette.filter_commands(event.text)
        else:
            palette.hide()

    def on_command_palette_command_selected(self, event: CommandPalette.CommandSelected) -> None:
        """用户从面板选取命令 → 填入输入框。"""
        input_box = self.query_one("#input", InputBox)
        input_box.value = event.command + " "
        input_box.focus()

    # ── 首页回调 ──────────────────────────────────────────────────────

    @on(NewSessionRequested)
    async def _handle_new_session(self, event: NewSessionRequested) -> None:
        """首页输入框回车 → 自动命名 → 进入对话并发送首条消息。"""
        msg = event.first_message
        info, session_dir = await self._kernel.create_session(msg)
        self._ingester.set_session(info.id)
        self._enter_session(session_id=info.id, name=info.name, first_message=msg)

    @on(SessionSelected)
    def _handle_session_selected(self, event: SessionSelected) -> None:
        """首页点击会话卡片 → 进入已有会话。"""
        self._enter_session(session_id=event.session_id, name=event.session_name)

    def _enter_session(self, session_id: str | None, name: str,
                       first_message: str = "") -> None:
        """进入对话页：设置会话名，关闭首页，可选自动发送首条消息。"""
        self._session.name = name

        # 设置会话标签
        label = self.query_one("#session-label", Static)
        label.update(f" {name}")

        # 已有会话：恢复上下文
        if session_id:
            self._ingester.set_session(session_id)
            self._session.is_ensured = True
            self._restore_session(session_id)

        # 关闭首页 Screen 回到对话页
        self.pop_screen()

        # 清空并重建消息列表
        msg_list = self.query_one("#messages", MessageList)
        msg_list.clear()

        # 已有会话：恢复 UI 消息（按轮重建树，保留 think/工具/正文细节）
        if session_id:
            msg_list.restore_conversation(self._restored_turns)

        # 更新状态栏
        self._update_status_bar()

        # 聚焦输入框
        self.query_one("#input", InputBox).focus()

        # 新会话：自动发送首条消息
        if first_message:
            self.call_later(self._send_first_message, first_message)

    def _send_first_message(self, text: str, images: list[str] | None = None) -> None:
        """新会话的首条消息：直接走对话流程。"""
        images = images or []
        msg_list = self.query_one("#messages", MessageList)
        input_box = self.query_one("#input", InputBox)

        if self.provider is None:
            msg_list.add_error(t("app.no_provider"))
            input_box.disabled = False
            input_box.focus()
            return

        content = build_user_content(text, images)
        self._session.last_user_text = text or t("app.image_msg", n=len(images))
        msg_list.add_user_message(text or "", file_paths=images)
        self._session.conversation.append({"role": "user", "content": content})
        input_box.disabled = True
        self.chat_worker()

    def _restore_session(self, session_id: str) -> None:
        """恢复已有会话的对话状态。

        conversation 供 LLM 上下文使用；_restored_turns 按轮保留
        think/工具/正文细节，供 UI 重建回合树。

        restore_session_full 一次读盘同时返回两者，避免同一批 turn
        文件被读两遍。
        """
        conv, turn, turns = restore_session_full(self._config.sessions_root, session_id)
        self._session.conversation = conv
        self._session.turn = turn
        self._restored_turns = turns

    # ── 用户输入 ──────────────────────────────────────────────────────

    async def on_input_box_user_submitted(self, event: InputBox.UserSubmitted) -> None:
        """用户发送消息 → 命令路由 或 chat_worker。"""
        if self._session.is_maintenance:
            return

        text = event.text
        file_paths: list[str] = list(event.file_paths if hasattr(event, 'file_paths') else [])
        clipboard_images = event.clipboard_images if hasattr(event, 'clipboard_images') else []
        msg_list = self.query_one("#messages", MessageList)
        input_box = self.query_one("#input", InputBox)
        self.query_one("#palette", CommandPalette).hide()

        # ── 确认流 ──
        if await self._cmd_handler.handle_confirmation(text, msg_list):
            return

        # ── / 命令路由 ──
        command = self._cmd_registry.route(text)
        if command is not None:
            cmd_def, args = command
            self._dispatch_command(cmd_def, args, msg_list, input_box, text)
            return

        # 以 / 开头但未匹配
        if text.startswith("/"):
            msg_list.add_user_message(text)
            msg_list.add_command_result(t("ui.widget.unknown_command", text=text))
            return

        # ── 正常对话（含多模态 / 文件附件） ──
        if clipboard_images:
            # 确保 session 存在
            if not self._session.is_ensured:
                info, session_dir = await self._kernel.create_session(text or t("app.image_msg_fallback"))
                self._ingester.set_session(info.id)
                self._session.is_ensured = True
                self._session.turn = 1
                self._session.name = info.name
                self.query_one("#session-label", Static).update(f" {info.name}")
            session_dir = self._ingester._session_dir
            saved = save_images_to_session(clipboard_images, session_dir)
            file_paths.extend(saved)

        # 合并所有文件路径
        all_files = file_paths  # 拖放文件 + 剪贴板图片保存后的文件

        content = build_user_content(text, all_files)
        self._session.last_user_text = text or t("app.files_attached", n=len(all_files))

        msg_list.add_user_message(text or "", file_paths=all_files)
        user_msg = {"role": "user", "content": content}
        if all_files:
            user_msg["_image_paths"] = all_files
        self._session.conversation.append(user_msg)

        if self.provider is None:
            msg_list.add_error(t("app.no_provider"))
            input_box.disabled = False
            input_box.focus()
            return

        input_box.disabled = True
        self.chat_worker()

    # ── Worker: 对话 ─────────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def chat_worker(self) -> None:
        """异步 worker：委托给 kernel.chat()。"""
        self._bridge.reset_text()

        try:
            # 延迟创建 session（首条消息时）
            if not self._session.is_ensured:
                info, session_dir = await self._kernel.create_session(self._session.last_user_text)
                self._ingester.set_session(info.id)
                self._session.is_ensured = True
                self._session.turn = 1
                self._session.name = info.name
                self.query_one("#session-label", Static).update(f" {info.name}")
            else:
                self._session.turn += 1
                session_dir = self._ingester._session_dir

            result = await self._kernel.chat(
                user_msg=self._session.last_user_text,
                session_dir=session_dir,
                turn=self._session.turn,
                conversation=self._session.conversation,
                ui=self._bridge,
            )

            self._session.conversation = result.conversation
            self._last_usage = result.usage  # 来自 agent.py 的准确上下文计数

        except Exception as e:
            msg_list = self.query_one("#messages", MessageList)
            if msg_list.has_pending():
                msg_list.finish_ai_message()
            msg_list.add_error(t("app.exec_error", e=e))
        finally:
            self._update_status_bar()
            input_box = self.query_one("#input", InputBox)
            input_box.disabled = False
            input_box.focus()

    # ── Worker: Reflect ──────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def reflect_worker(self) -> None:
        """统一反思：LLM 回顾对话 → 更新记忆 + 生成总览 → 用户审查。"""
        msg_list = self.query_one("#messages", MessageList)
        if not self._session.is_ensured:
            msg_list.add_command_result(t("app.reflect_no_changes"))
            self._cmd_handler.exit_maintenance()
            return
        try:
            session_dir = self._ingester._session_dir
            current_turn = self._session.turn

            result = await self._kernel.reflect(session_dir, current_turn)

            if result is None or not result.changes_detected:
                msg_list.add_command_result(t("app.reflect_no_changes"))
                self._cmd_handler.exit_maintenance()
                return

            # 展示变更并请求确认（简化版：直接应用）
            await self._kernel.apply_reflection(session_dir, result, current_turn)

            # 汇总展示
            from core.context.overview import parse_overview_md
            sections = parse_overview_md(result.overview)
            topics_key_zh, topics_key_en = "话题", "Topics"
            topics = (sections.get(topics_key_zh) or sections.get(topics_key_en) or [])

            updated_files = [
                fname for fname in result.proposed_files
                if result.proposed_files.get(fname) != result.current_files.get(fname)
            ]
            if updated_files:
                msg_list.add_command_result(
                    t("app.reflect_done") + "\n"
                    + t("app.compact_topics_line", topics=', '.join(topics[:3])) + "\n"
                    + "更新: " + ', '.join(f.replace('.md', '') for f in updated_files)
                )
            else:
                msg_list.add_command_result(
                    t("app.reflect_done") + "\n"
                    + t("app.compact_topics_line", topics=', '.join(topics[:3]))
                )
        except Exception as e:
            msg_list.add_error(t("app.reflect_error", e=e))
        finally:
            self._cmd_handler.exit_maintenance()
            self._update_status_bar()

    # ── UI 桥接方法（供 core 层 handler 调用，避免 core→ui 直接导入）───

    async def open_api_config_screen(self, edit_name: str | None = None) -> dict | None:
        """打开 API 配置屏幕，返回用户填写的配置 dict。用户取消返回 None。"""
        from .screens.api_config import ApiConfigScreen
        return await self.push_screen_wait(ApiConfigScreen(edit_name=edit_name))

    def refresh_command_palette(self) -> None:
        """刷新命令面板的命令列表（语言切换后重新加载翻译后的描述）。"""
        from .widgets.command_palette import CommandPalette
        palette = self.query_one("#palette", CommandPalette)
        palette.set_registry(self._cmd_registry)

    def refresh_status_bar_model(self, model: str | None = None, api_name: str | None = None) -> None:
        """更新状态栏的模型名和 API 名。"""
        if model is not None:
            self._model_name = model
        if api_name is not None:
            self._api_name = api_name
        self._update_status_bar()

    # ── 状态栏 ───────────────────────────────────────────────────────

    def _update_status_bar(self) -> None:
        """更新状态栏：token 可视化条 + 模型名。

        委托 StatusBar.update_from_session 自动估算 token 用量。
        """
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_from_session(
            usage=self._last_usage,
            conversation=self._session.conversation,
            tools_schema=self._tool_registry.get_schemas(),
            model=self._model_name,
            api_name=self._api_name,
            context_window=self._config.app.context_window,
        )

    # ── P3: 系统托盘 ─────────────────────────────────────────────────

    async def on_unmount(self) -> None:
        """应用关闭时停止 MCP 资源。"""
        if hasattr(self, '_mcp_adapter'):
            await self._mcp_adapter.stop_watcher()
            self._mcp_adapter.stop_health_check()
        if hasattr(self, '_store'):
            await self._store.close()

    # ── 全局快捷键 ───────────────────────────────────────────────────

    def action_go_home(self) -> None:
        """Esc → 首页 / 对话页 切换。"""
        if self._is_on_home():
            # 已在首页 → 返回对话页
            self.pop_screen()
        else:
            # 在对话页 → 去首页
            self.push_screen(HomeScreen())

    def _is_on_home(self) -> bool:
        """判断当前是否已在首页。"""
        return any(isinstance(s, HomeScreen) for s in self.screen_stack)

    @staticmethod
    def action_noop() -> None:
        """空操作（禁用 Ctrl+Q 退出）。"""
        pass
