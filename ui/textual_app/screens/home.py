"""首页 — ASCII 大标题 + 会话卡片 + 新会话输入框。

启动时显示。点击会话卡片进入对话，输入框回车开启新会话。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Input, Static

from core.locale import t
from core.setup import aide_dir

if TYPE_CHECKING:
    from textual.app import App

logger = logging.getLogger(__name__)

AIDE_ROOT = aide_dir()
SESSIONS_ROOT = AIDE_ROOT / "sessions"

# ── ASCII 大标题 (█ 全块字符组成 AIDE AGENT) ─────────────────────────

TITLE_ART = """\
 █████ █████ ████  █████      █████ █████ █████ █   █ █████ 
 █   █   █   █   █ █          █   █ █     █     ██  █   █   
 █████   █   █   █ █████      █████ █ ███ █████ █ █ █   █   
 █   █   █   █   █ █          █   █ █   █ █     █  ██   █   
 █   █ █████ ████  █████      █   █ █████ █████ █   █   █   """

# ── 会话卡片 ───────────────────────────────────────────────────────────


class CardClicked(Message, bubble=True):
    """会话卡片被点击。"""

    def __init__(self, session_id: str, session_name: str) -> None:
        self.session_id = session_id
        self.session_name = session_name
        super().__init__()


class SessionCard(Static):
    """单个会话卡片：名称 + 轮数 + 日期。"""

    def __init__(self, session_id: str, name: str, date_str: str,
                 **kwargs) -> None:
        self.session_id = session_id
        self.session_name = name
        super().__init__(**kwargs)
        # 构建卡片文本
        text = Text()
        text.append(f" {name}", style="bold")
        text.append(f"\n {date_str}", style="dim")
        self.update(text)

    def on_click(self) -> None:
        """点击时冒泡 CardClicked 消息。"""
        self.post_message(CardClicked(self.session_id, self.session_name))


class SessionSelected(Message):
    """用户点击了某个会话卡片。"""

    def __init__(self, session_id: str, session_name: str) -> None:
        self.session_id = session_id
        self.session_name = session_name
        super().__init__()


class NewSessionRequested(Message):
    """用户在首页输入消息 → 创建新会话并立即发送。"""

    def __init__(self, first_message: str) -> None:
        self.first_message = first_message
        super().__init__()


# ── 首页 ───────────────────────────────────────────────────────────────


class HomeScreen(Screen):
    """启动首页。

    布局：
      - 上方：ASCII 大标题
      - 中间：会话卡片列表（滚动）
      - 底部：新会话输入框
    """

    CSS = """
    HomeScreen {
        background: #0c0c0c;
        align: center middle;
    }

    #home-container {
        width: 60;
        height: auto;
        max-height: 100%;
    }

    #title-art {
        color: #ffffff;
        width: 1fr;
        content-align: center middle;
        margin: 2 0 1 0;
    }

    #session-list {
        width: 1fr;
        height: auto;
        max-height: 16;
        margin: 1 0;
        overflow-y: auto;
        scrollbar-size: 0 0;
    }

    .session-card {
        width: 1fr;
        margin: 0 0 1 0;
        padding: 1 1;
        border: solid #888888;
        background: #121212;
        color: #ffffff;
    }

    .session-card:hover {
        border: solid #ffffff;
        background: #1a1a1a;
    }

    .session-placeholder {
        width: 1fr;
        text-align: center;
        color: #888888;
        margin: 2 0;
    }

    #new-session-input {
        width: 1fr;
        margin: 1 0;
        padding: 0 1;
        border: solid #888888;
        background: #121212;
        color: #ffffff;
    }

    #new-session-input:focus {
        border: solid #ffffff;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="home-container"):
            yield Static(TITLE_ART, id="title-art")
            yield Input(
                placeholder=t("ui.home.input_placeholder"),
                id="new-session-input",
            )
            yield SessionList(id="session-list")

    def on_mount(self) -> None:
        """加载已有会话列表。"""
        self._load_sessions()
        self.query_one("#new-session-input", Input).focus()

    def _load_sessions(self) -> None:
        """扫描 ~/.aide/sessions/ 加载会话卡片（按最后活动时间倒序）。"""
        session_list = self.query_one("#session-list", SessionList)

        if not SESSIONS_ROOT.exists():
            session_list.show_placeholder(t("ui.home.no_sessions"))
            return

        sessions: list[tuple[str, str, str, str]] = []  # (id, name, date, sort_key)
        for session_dir in SESSIONS_ROOT.iterdir():
            if not session_dir.is_dir():
                continue
            sid = session_dir.name

            # ── 最后活动时间（用于排序和显示） ──
            sort_key = ""   # ISO 时间戳，用于排序
            date_str = ""   # 显示日期

            # 1) 从 messages/ 目录获取最新 turn 文件的 mtime
            messages_dir = session_dir / "messages"
            if messages_dir.is_dir():
                turn_files = sorted(messages_dir.glob("turn_*.json"))
                if turn_files:
                    mtime = turn_files[-1].stat().st_mtime
                    dt = datetime.fromtimestamp(mtime)
                    sort_key = dt.isoformat()
                    date_str = dt.strftime("%Y-%m-%d")

            # 2) 回退：timeline.json 最后条目
            if not sort_key:
                timeline = session_dir / "timeline.json"
                if timeline.exists():
                    try:
                        data = json.loads(timeline.read_text(encoding="utf-8"))
                        if data:
                            ts = data[-1].get("timestamp", "")
                            if ts:
                                sort_key = ts
                                try:
                                    dt = datetime.fromisoformat(ts)
                                    date_str = dt.strftime("%Y-%m-%d")
                                except ValueError:
                                    date_str = ts[:10]
                    except (json.JSONDecodeError, OSError):
                        pass

            # 3) 最终回退：目录 mtime
            if not sort_key:
                mtime = session_dir.stat().st_mtime
                dt = datetime.fromtimestamp(mtime)
                sort_key = dt.isoformat()

            if not date_str:
                try:
                    date_str = f"{sid[:4]}-{sid[4:6]}-{sid[6:8]}"
                except (IndexError, ValueError):
                    date_str = "—"

            name = self._derive_name(session_dir, sid)
            sessions.append((sid, name, date_str, sort_key))

        if not sessions:
            session_list.show_placeholder(t("ui.home.no_sessions"))
            return

        # 按最后活动时间倒序排列
        sessions.sort(key=lambda s: s[3], reverse=True)

        # 限制最近 20 个会话
        for sid, name, date_str, _ in sessions[:20]:
            card = SessionCard(sid, name, date_str)
            card.add_class("session-card")
            session_list.mount(card)

    def _derive_name(self, session_dir: Path, session_id: str) -> str:
        """从会话数据推导名称。"""
        # 优先 meta.json（自动命名）
        meta = session_dir / "meta.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                name = data.get("name", "")
                if name:
                    return name[:30]
            except (json.JSONDecodeError, OSError):
                pass

        # 其次 overview.md 话题
        overview = session_dir / "overview.md"
        if overview.exists():
            try:
                from core.context.compactor import parse_overview_md
                text = overview.read_text(encoding="utf-8")
                sections = parse_overview_md(text)
                topics = sections.get("话题", []) or sections.get("Topics", [])
                if topics:
                    return topics[0][:30]
            except (OSError, Exception):
                pass

        # 再次 timeline 首条摘要
        timeline = session_dir / "timeline.json"
        if timeline.exists():
            try:
                data = json.loads(timeline.read_text(encoding="utf-8"))
                if data:
                    summary = data[0].get("summary", "")
                    if summary and len(summary) > 2:
                        return summary[:30]
            except (json.JSONDecodeError, OSError):
                pass

        # 最后用 session ID 格式化
        try:
            return f"{session_id[:4]}-{session_id[4:6]}-{session_id[6:8]}"
        except (IndexError, ValueError):
            return session_id[:15]

    # ── 事件处理 ──────────────────────────────────────────────────────

    @on(Input.Submitted, "#new-session-input")
    def _on_new_session(self, event: Input.Submitted) -> None:
        """输入框回车 → 创建新会话并发送首条消息。"""
        text = event.value.strip()
        if text:
            event.stop()
            self.post_message(NewSessionRequested(text))

    @on(CardClicked)
    def _on_card_clicked(self, event: CardClicked) -> None:
        """会话卡片被点击 → 进入该会话。"""
        event.stop()
        self.post_message(SessionSelected(
            event.session_id,
            event.session_name,
        ))


# ── 会话列表容器 ───────────────────────────────────────────────────────


class SessionList(Vertical):
    """会话卡片列表容器。"""

    def show_placeholder(self, text: str) -> None:
        """显示占位提示。"""
        placeholder = Static(text)
        placeholder.add_class("session-placeholder")
        self.mount(placeholder)
