"""Aide Agent 入口脚本 — 单实例运行。

用法:
    uv run python core/main.py            # 启动 TUI + 托盘守护
    uv run python core/main.py --no-daemon  # 仅 TUI，不拉托盘守护（调试用）
    aide

第二次运行 aide 时，不会启动新实例，而是激活已有窗口。
"""

import os
import sys
from pathlib import Path

# 开发模式路径处理（PyInstaller bundle 中 sys.frozen 已设，跳过）：
# 1) 把脚本所在目录（core/）从 sys.path 移除——否则 `import locale` 会解析到
#    core/locale.py 遮蔽标准库 locale，textual 等依赖 stdlib locale 的模块
#    相对导入直接崩溃；
# 2) 注入项目根目录，使 `python core/main.py` 脱离 uv 也能运行。
# 比较用 os.path.normcase：Windows 上 Path.resolve() 返回磁盘实际大小写
# （盘符大写），而 sys.path[0] 保留启动时传参大小写（Git Bash 等小写），
# 字符串直接比较会失配导致 core/ 没被移除、`import locale` 崩。
if not getattr(sys, "frozen", False):
    _here = Path(__file__).resolve().parent
    _here_norm = os.path.normcase(str(_here))
    sys.path[:] = [p for p in sys.path if os.path.normcase(str(p)) != _here_norm]
    _project_root = _here.parent
    _root_norm = os.path.normcase(str(_project_root))
    if not any(os.path.normcase(str(p)) == _root_norm for p in sys.path):
        sys.path.insert(0, str(_project_root))

from core.resources import is_bundled

from core.setup import aide_dir, ensure_aide_root
from core.launcher import (
    acquire_instance_lock,
    decorate_console,
    ensure_daemon,
)

_LOCK_FILE = aide_dir() / "aide.pid"
_DAEMON_LOCK = aide_dir() / "daemon.pid"


# ── 烟雾测试 ────────────────────────────────────────────────────────────────

def _smoke_test() -> None:
    """烟雾测试：验证所有关键模块可导入 + 资源路径正确。"""
    errors: list[str] = []

    # 1. 核心模块导入
    for mod_name in [
        "core.setup", "core.config", "core.storage", "core.resources",
        "core.platform", "core.locale", "core.locale_data",
        "core.launcher",
        "core.kernel.agent", "core.kernel.protocols",
        "core.kernel.bootstrap", "core.kernel.fc_loop", "core.kernel.context",
        "core.context.pipeline", "core.context.ingester",
        "core.context.relevance", "core.context.token_counter",
        "core.memory.reflector", "core.memory.recall",
        "core.llm_gateway.provider", "core.llm_gateway.openai_provider",
        "core.llm_gateway.ollama_provider", "core.llm_gateway.image_utils",
        "core.llm_gateway.content_builder",
        "core.commands", "core.commands.builtin.handlers",
        "core.commands.builtin.settings_handlers",
        "core.commands.builtin.mcp_handlers",
        "core.commands.builtin.plugin_commands",
        "core.plugins.contract", "core.plugins.host", "core.plugins.sdk",
        "core.plugins.slots",
        "core.tools", "core.tools.discovery", "core.tools.retry",
        "core.tools.read_file", "core.tools.write_file",
        "core.tools.run_shell",
        "core.tools.search_memory", "core.tools.web",
        "core.tools.search_in_files",
        "core.mcp.adapter", "core.mcp.protocol",
        "core.mcp.transport", "core.mcp.fault",
        "core.mcp.watcher", "core.mcp.lifecycle",
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

    no_daemon = "--no-daemon" in sys.argv

    ensure_aide_root()

    if not acquire_instance_lock(_LOCK_FILE):
        print("Aide is already running.")
        return

    decorate_console(Path(__file__).parent.parent / "Aide.ico")

    if not no_daemon:
        daemon_script = Path(__file__).parent / "tray_daemon.py"
        ensure_daemon(_DAEMON_LOCK, daemon_script)
    else:
        print("(--no-daemon) tray daemon skipped — run 'aide' for tray support.")

    from ui.textual_app.app import AideApp
    app = AideApp()
    app.run()


if __name__ == "__main__":
    main()
