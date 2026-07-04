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
from core.context.token_counter import compute_context_usage
from core.sessions.restorer import restore_session

from .widgets.input_box import InputBox
from .widgets.message_list import MessageList
from .widgets.command_palette import CommandPalette
from .widgets.status_bar import StatusBar
from .screens.onboarding import OnboardingScreen
from .screens.home import HomeScreen, SessionSelected, NewSessionRequested
from .tray import TrayManager
from .platform import hide_console
from .bridge import UIBridge
from .session_context import SessionContext
from .command_handler import CommandHandler

logger = logging.getLogger(__name__)


class AideApp(App):
    """Aide Agent P3 — 前端。"""

    TITLE = "Aide"

    BINDINGS = [
        ("escape", "go_home", t("app.return_home")),
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
        self._last_usage: TokenUsage | None = None  # 来自 ChatResult 的 token 用量

        # ── 状态栏 + 托盘 + 冷启动引导 ──
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_info(model=self._model_name, api_name=self._api_name)

        self._tray = TrayManager(self)
        self._tray.start()

        self._startup_worker()

    @work(exclusive=True, thread=False)
    async def _startup_worker(self) -> None:
        """启动 worker：智能跳过已有配置 → 冷启动检查 → 引导 → 首页。
        启动完成后自动最小化到系统托盘。"""
        if has_existing_config():
            self.push_screen(HomeScreen())
        elif is_cold_start():
            await self.push_screen_wait(OnboardingScreen())
            self._reload_after_onboarding()
            self.push_screen(HomeScreen())
        else:
            self.push_screen(HomeScreen())

        # 默认后台模式：启动完成后最小化到托盘
        self.action_hide_to_tray()

    def _reload_after_onboarding(self) -> None:
        """冷启动完成后重新加载配置和 provider。

        AppBootstrap.init() 在 OnboardingScreen 之前执行，那时
        settings.json 还不存在，provider 为 None。向导写入配置后
        需要重新加载，否则 provider 永远是空壳。
        """
        from core.config import Config
        from core.llm_gateway import create_provider
        from .widgets.status_bar import StatusBar

        config = Config.load()
        self._config = config
        set_locale(config.app.locale)

        try:
            self.provider = create_provider(config.llm)
            self._model_name = config.llm.model or config.llm.provider
        except Exception as e:
            logger.warning(t("app.provider_init_failed", e=e))

        # 更新 pipeline 参数（用户可能在设置中调整了 window_turns 等）
        self._pipeline.window_turns = config.app.window_turns
        self._pipeline.relevance_threshold = config.app.relevance_threshold

        # 更新内核中的 provider 引用（kernel / fc_loop / compactor / updater）
        self._kernel.set_provider(self.provider)
        self._kernel._fc_loop.max_turns = config.app.max_turns

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
        self._ingester.ensure_session(info.id)
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
            self._ingester.ensure_session(session_id)
            self._session.is_ensured = True
            self._restore_session(session_id)

        # 关闭首页 Screen 回到对话页
        self.pop_screen()

        # 清空并重建消息列表
        msg_list = self.query_one("#messages", MessageList)
        msg_list.clear()

        # 已有会话：恢复 UI 消息
        if session_id:
            msg_list.restore_conversation(self._session.conversation)

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
        """恢复已有会话的对话状态。"""
        conv, turn = restore_session(self._config.sessions_root, session_id)
        self._session.conversation = conv
        self._session.turn = turn

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
            await self._cmd_handler.run_command(cmd_def, args, msg_list, input_box, text)
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
                self._ingester.ensure_session(info.id)
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
                self._ingester.ensure_session(info.id)
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

    # ── Worker: Profile Update ───────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def profile_update_worker(self) -> None:
        msg_list = self.query_one("#messages", MessageList)
        try:
            results = await self._kernel.update_profile()
            updated = [k for k, v in results.items() if v]
            if updated:
                msg_list.add_command_result(t("app.profile_updated", names=', '.join(updated)))
            else:
                msg_list.add_command_result(t("app.profile_no_update"))
        except Exception as e:
            msg_list.add_error(t("app.profile_update_failed", e=e))
        finally:
            self._cmd_handler.exit_maintenance()

    # ── Worker: Compress ──────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def compress_worker(self) -> None:
        msg_list = self.query_one("#messages", MessageList)
        if not self._session.is_ensured:
            msg_list.add_command_result(t("app.no_data_to_compact"))
            self._cmd_handler.exit_maintenance()
            return
        try:
            overview = await self._kernel.compact_session(self._ingester._session_dir)
            if overview:
                from core.context.compactor import parse_overview_md
                sections = parse_overview_md(overview)
                # 兼容双语 section 标题（压缩时的语言可能和当前不同）
                topics_key_zh = "话题"
                topics_key_en = "Topics"
                prefs_key_zh = "用户偏好"
                prefs_key_en = "User Preferences"
                decisions_key_zh = "决策与结论"
                decisions_key_en = "Decisions & Conclusions"
                topics = (sections.get(topics_key_zh) or sections.get(topics_key_en) or [])
                prefs = (sections.get(prefs_key_zh) or sections.get(prefs_key_en) or [])
                decisions = (sections.get(decisions_key_zh) or sections.get(decisions_key_en) or [])
                msg_list.add_command_result(
                    t("app.compact_done") + "\n\n"
                    + t("app.compact_topics_line", topics=', '.join(topics[:3])) + "\n"
                    + t("app.compact_prefs_line", n=len(prefs)) + "\n"
                    + t("app.compact_decisions_line", n=len(decisions))
                )
            else:
                msg_list.add_command_result(t("app.compact_failed"))
        except Exception as e:
            msg_list.add_error(t("app.compact_error", e=e))
        finally:
            self._cmd_handler.exit_maintenance()
            self._update_status_bar()

    # ── 状态栏 ───────────────────────────────────────────────────────

    def _update_status_bar(self) -> None:
        """更新状态栏：token 可视化条 + 模型名。

        Chat 后使用 agent.py 的准确计数（system + trimmed_conv + tools）。
        未聊天时（恢复会话等）回退到从 conversation 估算，避免始终显示 0。
        """
        context_window = self._config.app.context_window

        if self._last_usage is not None:
            estimated = self._last_usage.total_tokens
            pct = self._last_usage.context_pct
        elif self._session.conversation:
            # 会话恢复 / 尚未聊天 → 从 conversation 估算
            # 不含 system prompt，因此偏低，但比显示 0 好
            tools_schema = self._tool_registry.get_schemas()
            estimated, pct = compute_context_usage(
                self._session.conversation, tools_schema,
                context_window=context_window,
            )
        else:
            estimated, pct = 0, 0.0

        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_info(
            tokens=estimated, token_pct=pct,
            model=self._model_name,
            api_name=self._api_name,
            context_window=context_window,
        )

    # ── P3: 系统托盘 ─────────────────────────────────────────────────

    async def on_unmount(self) -> None:
        """应用关闭时停止托盘和 MCP 资源。"""
        if hasattr(self, '_tray'):
            self._tray.stop()
        if hasattr(self, '_mcp_adapter'):
            self._mcp_adapter.stop_watcher()
            self._mcp_adapter.stop_health_check()
        if hasattr(self, '_store'):
            await self._store.close()

    def action_restore(self) -> None:
        """恢复窗口（从托盘）。"""
        try:
            self.screen.refresh()
        except Exception:
            pass

    def action_hide_to_tray(self) -> None:
        """隐藏到托盘。各平台尽力隐藏控制台/终端窗口。"""
        hide_console()
        self.notify(t("app.tray_hidden"))

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

    def action_quit(self) -> None:
        """退出应用。"""
        if hasattr(self, '_tray'):
            self._tray.stop()
        self.exit()
