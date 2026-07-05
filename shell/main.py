"""Aide Agent 入口脚本 — 单实例运行。

用法:
    uv run python shell/main.py
    aide

第二次运行 aide 时，不会启动新实例，而是激活已有窗口。
"""

import atexit
import ctypes
import os
import sys
from pathlib import Path

# 确保项目根目录在 Python path 中（仅开发模式，PyInstaller bundle 中跳过）
from core.resources import is_bundled
if not is_bundled():
    _project_root = Path(__file__).parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

from core.setup import aide_dir, ensure_aide_root
from core.platform import IS_WINDOWS

_LOCK_FILE = aide_dir() / "aide.pid"


# ── 单实例锁 ────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    """检查 PID 是否存活。"""
    try:
        if IS_WINDOWS:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def _bring_to_front(title: str) -> bool:
    """将已有窗口提到最前。仅 Windows。"""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _acquire_lock() -> bool:
    """尝试获取单实例锁。已有人持锁则激活其窗口并返回 False。"""
    if _LOCK_FILE.exists():
        try:
            pid = int(_LOCK_FILE.read_text().strip())
            if _pid_alive(pid):
                # 已有实例运行中 → 激活窗口
                _bring_to_front("Aide Agent")
                return False
            # 僵尸锁（进程已死）→ 删除
            _LOCK_FILE.unlink(missing_ok=True)
        except (ValueError, OSError):
            _LOCK_FILE.unlink(missing_ok=True)

    # 写入自己的 PID
    _LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)
    return True


def _release_lock() -> None:
    """释放单实例锁。"""
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── 控制台装饰 ──────────────────────────────────────────────────────────────

def _decorate_console() -> None:
    """设置控制台窗口标题和图标（仅 Windows）。"""
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.SetConsoleTitleW("Aide Agent")

        ico = Path(__file__).parent.parent / "Aide.ico"
        if not ico.exists():
            return

        hwnd = kernel32.GetConsoleWindow() or user32.FindWindowW(None, "Aide Agent")
        if not hwnd:
            return

        hicon = user32.LoadImageW(None, str(ico), 1, 32, 32, 0x00000010)
        if hicon:
            user32.SendMessageW(hwnd, 0x0080, 0, hicon)  # ICON_SMALL
            user32.SendMessageW(hwnd, 0x0080, 1, hicon)  # ICON_BIG
    except Exception:
        pass


# ── 守护进程 ──────────────────────────────────────────────────────────────────

_DAEMON_LOCK = aide_dir() / "daemon.pid"


def _ensure_daemon() -> None:
    """确保托盘守护进程在后台运行。已运行则跳过。"""
    if _DAEMON_LOCK.exists():
        try:
            pid = int(_DAEMON_LOCK.read_text().strip())
            if _pid_alive(pid):
                return  # 已在运行
        except (ValueError, OSError):
            pass

    import subprocess
    daemon = Path(__file__).parent / "tray_daemon.py"
    if not daemon.exists():
        return

    if IS_WINDOWS:
        # pythonw: 无控制台窗口
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        if not pythonw.exists():
            pythonw = sys.executable
        subprocess.Popen(
            [str(pythonw), str(daemon)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        subprocess.Popen(
            [sys.executable, str(daemon)],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def _smoke_test() -> None:
    """烟雾测试：验证所有关键模块可导入 + 资源路径正确。"""
    errors: list[str] = []

    # 1. 核心模块导入
    for mod_name in [
        "core.setup", "core.config", "core.storage", "core.resources",
        "core.platform", "core.locale", "core.locale_data",
        "core.kernel.agent", "core.kernel.state", "core.kernel.protocols",
        "core.kernel.bootstrap", "core.kernel.fc_loop", "core.kernel.context",
        "core.context.pipeline", "core.context.ingester",
        "core.context.compactor", "core.context.relevance",
        "core.context.embeddings", "core.context.token_counter",
        "core.memory.capture", "core.memory.entries", "core.memory.updater",
        "core.memory.recall", "core.memory.tracker",
        "core.llm_gateway.provider", "core.llm_gateway.openai_provider",
        "core.llm_gateway.ollama_provider", "core.llm_gateway.image_utils",
        "core.llm_gateway.content_builder",
        "core.commands", "core.commands.builtin.handlers",
        "core.commands.builtin.settings_handlers",
        "core.commands.builtin.mcp_handlers",
        "core.commands.builtin.plugin_commands",
        "core.commands.builtin._compat",
        "core.plugins.contract", "core.plugins.host", "core.plugins.sdk",
        "core.plugins.slots",
        "core.tools", "core.tools.discovery", "core.tools.retry",
        "core.tools.builtin.read_file", "core.tools.builtin.write_file",
        "core.tools.builtin.edit_file", "core.tools.builtin.run_shell",
        "core.tools.builtin.search_memory", "core.tools.builtin.web_search",
        "core.tools.builtin.web_fetch", "core.tools.builtin.list_dir",
        "core.tools.builtin.search_in_files", "core.tools.builtin.clipboard",
        "core.tools.mcp.adapter", "core.tools.mcp.protocol",
        "core.tools.mcp.transport", "core.tools.mcp.fault",
        "core.tools.mcp.watcher",
        "core.sessions.manager", "core.sessions.restorer",
    ]:
        try:
            __import__(mod_name)
        except ImportError as e:
            errors.append(f"IMPORT {mod_name}: {e}")

    # 2. UI 模块（可能因缺少图形环境失败，仅导入检查）
    for mod_name in [
        "ui.textual_app.app", "ui.textual_app.bridge",
        "ui.textual_app.platform", "ui.textual_app.command_handler",
        "ui.textual_app.screens.home", "ui.textual_app.screens.onboarding",
        "ui.textual_app.widgets.message_list", "ui.textual_app.widgets.input_box",
        "ui.textual_app.widgets.command_palette", "ui.textual_app.widgets.status_bar",
    ]:
        try:
            __import__(mod_name)
        except ImportError as e:
            errors.append(f"IMPORT {mod_name}: {e}")

    # 3. 资源路径验证（仅检查 datas 列表中的文件，不含 PYZ 中的 Python 模块）
    from core.resources import get_resource_path
    for name, rel in [
        ("CSS", "ui/textual_app/app.tcss"),
        ("插件模板", "core/plugins/templates/hello-plugin"),
        ("MCP 配置", "mcp/servers.json"),
    ]:
        p = get_resource_path(rel)
        if not p.exists():
            errors.append(f"RESOURCE {name}: not found at {p}")

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)

    print("SMOKE TEST PASSED")
    sys.exit(0)


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    # 烟雾测试模式：导入所有模块 + 检查资源 → 退出
    if "--smoke-test" in sys.argv:
        _smoke_test()
        return  # unreachable, _smoke_test calls sys.exit

    ensure_aide_root()

    if not _acquire_lock():
        print("Aide is already running. Activated existing window.")
        return

    _decorate_console()
    _ensure_daemon()

    from ui.textual_app.app import AideApp
    app = AideApp()
    app.run()


if __name__ == "__main__":
    main()
