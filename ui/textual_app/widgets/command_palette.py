"""命令面板 — 输入 / 时在输入框上方显示匹配命令列表。

支持：
  - 按输入内容实时过滤
  - 鼠标滚轮滚动
  - 点击选取并自动填入输入框

P4 Batch 2: 从 CommandRegistry 读取命令列表（含插件命令）。
"""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, ListView, ListItem
from textual.message import Message
from textual.events import Click

from core.commands.builtin.handlers import COMMANDS
from core.locale import t


class CommandItem(ListItem):
    """单条命令项 — 左侧命令名 + 右侧描述。"""

    def __init__(self, cmd: str, desc: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cmd = cmd
        self.desc = desc

    def compose(self) -> ComposeResult:
        label = f" {self.cmd:<20} {self.desc}"
        yield Static(label)


class CommandPalette(Vertical):
    """命令建议面板。

    默认隐藏，输入 / 时自动弹出。
    显示匹配的命令列表，超出时鼠标滚轮滚动。
    """

    DEFAULT_CSS = """
    CommandPalette {
        display: none;
        height: auto;
        max-height: 16;
        margin: 0 2 0 2;
        border: solid #555555;
        background: #121212;
        padding: 0 1;
        overflow-y: auto;
        scrollbar-size: 0 0;
        scrollbar-background: #121212;
        scrollbar-color: #121212;
    }

    CommandPalette.-visible {
        display: block;
    }

    CommandPalette ListView {
        height: auto;
        background: #121212;
        scrollbar-size: 0 0;
        scrollbar-background: #121212;
        scrollbar-color: #121212;
    }

    CommandPalette ListView > ListItem {
        padding: 0 1;
        color: #888888;
    }

    CommandPalette ListView > ListItem.-highlight {
        background: #1a2a3a;
        color: #c8c8c0;
    }

    CommandPalette Static {
        color: #888888;
    }
    """

    class CommandSelected(Message):
        """用户选取了一条命令。"""

        def __init__(self, command: str) -> None:
            self.command = command
            super().__init__()

    def __init__(self, cmd_registry=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cmd_registry = cmd_registry
        self._all_commands: list[tuple[str, str]] = []
        self._refresh_commands()

    def _refresh_commands(self) -> None:
        """从 CommandRegistry 刷新命令列表（含插件命令）。"""
        all_cmds: list[tuple[str, str]] = []

        if self._cmd_registry is not None:
            for cmd_def in self._cmd_registry.list_all():
                all_cmds.append((cmd_def.name, cmd_def.description))
        else:
            # 回退到模块级 COMMANDS dict
            all_cmds = [(cmd, desc) for cmd, (_, desc) in COMMANDS.items()]

        # 按命令名字母排序
        all_cmds.sort(key=lambda x: x[0])
        self._all_commands = all_cmds

    def set_registry(self, cmd_registry) -> None:
        """设置/更新 CommandRegistry 引用并刷新命令列表。"""
        self._cmd_registry = cmd_registry
        self._refresh_commands()

    def filter_commands(self, text: str) -> None:
        """根据输入文本过滤命令列表。

        路由规则：
          - 单个 / → 只显示内置命令（/help, /profile…）
          - // → 只显示技能命令（//pptx, //docx…）
          - /xxx → 在内置命令中过滤
          - //xxx → 在技能命令中过滤
        """
        # 每次过滤前刷新，确保插件加载后的新命令可见
        self._refresh_commands()

        text = text.strip()
        is_double = text.startswith("//")
        search = text.lstrip("/").strip()

        if is_double:
            # 只显示 // 技能命令
            pool = [(c, d) for c, d in self._all_commands if c.startswith("//")]
        else:
            # 只显示 / 内置命令（排除 // 技能命令）
            pool = [(c, d) for c, d in self._all_commands if not c.startswith("//")]

        if not search:
            matches = pool
        else:
            matches = [(c, d) for c, d in pool if search in c or c.startswith(text)]

        list_view = self.query_one(ListView)
        list_view.clear()

        if not matches:
            self.add_class("-visible")
            list_view.mount(ListItem(Static(t("ui.widget.no_match"))))
            return

        for cmd, desc in matches:
            item = CommandItem(cmd, desc)
            list_view.mount(item)

        self.add_class("-visible")

    def show(self) -> None:
        """显示面板。"""
        self.add_class("-visible")

    def hide(self) -> None:
        """隐藏面板。"""
        self.remove_class("-visible")

    def is_visible(self) -> bool:
        return self.has_class("-visible")

    def compose(self) -> ComposeResult:
        yield ListView()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """用户选中命令（Enter 或点击）。"""
        if event.item is not None and isinstance(event.item, CommandItem):
            self.post_message(self.CommandSelected(event.item.cmd))
            self.hide()
