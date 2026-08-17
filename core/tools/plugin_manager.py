"""plugin — 插件管理工具。

主 agent 可自行管理插件：列出已安装、从本地目录/zip 安装、加载/卸载。
install 的 path 由用户提供（工具不主动拉取任何来源——Aide 只声明格式规范，
社区按格式制作插件后放入目录或交给主 agent install）。

通过 ToolContext.plugin_host 注入（bootstrap Phase 4 后补入）。
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from .definition import ToolDefinition

# 可识别为插件根的标志文件（与 FormatDetector 一致）
_MANIFEST_MARKERS = ("aide.plugin.json", "SKILL.md", ".claude-plugin/plugin.json")

_PLUGIN_DESC = (
    "管理 Aide 插件：列出已安装插件（list）、从本地目录或 zip 安装（install，path 由用户提供）、"
    "加载/卸载（load/unload）。"
)


def _is_plugin_root(d: Path) -> bool:
    """目录是否含可识别的插件清单。"""
    return any((d / m).exists() for m in _MANIFEST_MARKERS)


def _find_plugin_root(candidate: Path) -> Path | None:
    """在 candidate 或其直接子目录中定位插件根。"""
    if _is_plugin_root(candidate):
        return candidate
    for sub in candidate.iterdir():
        if sub.is_dir() and _is_plugin_root(sub):
            return sub
    return None


def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    """解压 zip 到 dest，拒绝路径逃逸条目。"""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest)):
                raise ValueError(f"zip 包含不安全路径: {name}")
        zf.extractall(dest)


def _format_list(ctx) -> str:
    """列出已发现的插件与加载状态。"""
    host = ctx.plugin_host
    manifests = host.discover()
    loaded = {info.id for info in host.list_loaded()}
    lines = ["插件列表："]
    if not manifests:
        lines.append("  （无已安装插件——用 install 从本地目录或 zip 安装）")
    for m in manifests:
        mark = "● 已加载" if m.id in loaded else "○ 未加载"
        lines.append(f"  {mark} {m.id} [{m.kind}] — {(m.description or '')[:60]}")
    lines.append(f"插件目录：{host._config.plugins_dir}")
    return "\n".join(lines)


async def _install_plugin(path_str: str, ctx) -> str:
    """从本地目录或 zip 安装插件到 plugins_dir，并自动加载。"""
    host = ctx.plugin_host
    src = Path(path_str).expanduser()
    if not src.exists():
        return f"错误：路径不存在：{path_str}"
    if not src.is_dir() and src.suffix.lower() != ".zip":
        return "错误：仅支持本地目录或 .zip 文件"

    plugins_dir = host._config.plugins_dir
    plugins_dir.mkdir(parents=True, exist_ok=True)

    try:
        if src.is_dir():
            root = _find_plugin_root(src)
            if root is None:
                return f"错误：{path_str} 不是有效的插件目录（缺少 SKILL.md / aide.plugin.json / .claude-plugin）"
            dest = plugins_dir / root.name
            if dest.exists():
                return f"错误：插件 {root.name} 已存在于插件目录"
            shutil.copytree(root, dest)
            installed_name = root.name
        else:  # zip
            with tempfile.TemporaryDirectory() as tmp:
                extract_dir = Path(tmp)
                _safe_extract_zip(src, extract_dir)
                root = _find_plugin_root(extract_dir)
                if root is None:
                    return f"错误：{path_str} 解压后未找到插件清单"
                dest = plugins_dir / root.name
                if dest.exists():
                    return f"错误：插件 {root.name} 已存在于插件目录"
                shutil.copytree(root, dest)
                installed_name = root.name
    except ValueError as e:
        return f"错误：{e}"
    except (OSError, zipfile.BadZipFile) as e:
        return f"错误：安装失败：{e}"

    # 自动加载新插件（discover 找到匹配 manifest.id 的）
    for m in host.discover():
        if m.root_dir == (plugins_dir / installed_name):
            info = await host.load(m.id)
            if info:
                return f"已安装并加载插件：{m.id}（目录 {installed_name}）"
            return f"已安装插件：{m.id}，但加载失败（见日志）"
    return f"已复制到插件目录：{installed_name}，但未识别为有效插件"


async def _load_plugin(plugin_id: str, ctx) -> str:
    host = ctx.plugin_host
    info = await host.load(plugin_id)
    if info:
        return f"插件已加载：{plugin_id}"
    return f"错误：加载插件失败：{plugin_id}"


async def _unload_plugin(plugin_id: str, ctx) -> str:
    host = ctx.plugin_host
    ok = await host.unload(plugin_id)
    return f"插件已卸载：{plugin_id}" if ok else f"错误：卸载失败或插件不存在：{plugin_id}"


async def execute(arguments: dict, ctx=None) -> str:
    """执行插件管理操作。

    ctx.plugin_host 由 ToolContext 注入；未注入（无插件运行时）返回不可用。
    """
    host = getattr(ctx, "plugin_host", None) if ctx else None
    if host is None:
        return "错误：插件系统不可用（plugin_host 未注入）"

    action = str(arguments.get("action", "")).strip()
    if action == "list":
        return _format_list(ctx)
    if action == "install":
        path_str = str(arguments.get("path", "")).strip()
        if not path_str:
            return "错误：install 需要 path 参数（本地插件目录或 zip 路径）"
        return await _install_plugin(path_str, ctx)
    if action == "load":
        pid = str(arguments.get("plugin_id", "")).strip()
        if not pid:
            return "错误：load 需要 plugin_id 参数"
        return await _load_plugin(pid, ctx)
    if action == "unload":
        pid = str(arguments.get("plugin_id", "")).strip()
        if not pid:
            return "错误：unload 需要 plugin_id 参数"
        return await _unload_plugin(pid, ctx)
    return (
        "错误：未知 action，支持 list / install / load / unload。"
        "install 需要用户提供的 path（本地目录或 zip）。"
    )


definition = ToolDefinition(
    name="plugin",
    description=_PLUGIN_DESC,
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "install", "load", "unload"],
                "description": "操作类型：list 列插件；install 安装（需 path）；load/unload 管理加载状态",
            },
            "path": {
                "type": "string",
                "description": "install 时使用：本地插件目录或 .zip 的绝对路径（由用户提供）",
            },
            "plugin_id": {
                "type": "string",
                "description": "load/unload 时使用：插件 id",
            },
        },
        "required": ["action"],
    },
    execute=execute,
)
