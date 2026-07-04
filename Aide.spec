# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 将 Aide 打包为独立目录（onedir）。

构建:
    uv run python -m PyInstaller Aide.spec --noconfirm
    或: uv run python scripts/build.py
"""

import sys
from pathlib import Path

# ── 项目根 ──
PROJECT_ROOT = Path(__file__).parent.absolute()

# ── 数据文件 ──
datas = [
    # 应用 CSS（Textual 通过 inspect.getfile + relative 解析）
    (str(PROJECT_ROOT / "ui" / "textual_app" / "app.tcss"), "ui/textual_app"),
    # 插件模板（首次启动时复制到 ~/.aide/plugins/）
    (str(PROJECT_ROOT / "core" / "plugins" / "templates"), "core/plugins/templates"),
    # 默认 MCP 配置
    (str(PROJECT_ROOT / "mcp" / "servers.json"), "mcp"),
]

# ONNX 模型（构建前由 scripts/build.py 下载到 models/）
model_dir = PROJECT_ROOT / "models" / "all-MiniLM-L6-v2"
if (model_dir / "model.onnx").exists():
    datas.append((str(model_dir / "model.onnx"), "models/all-MiniLM-L6-v2"))
    datas.append((str(model_dir / "vocab.txt"), "models/all-MiniLM-L6-v2"))
else:
    print("警告: ONNX 模型未找到，请先运行 scripts/build.py --no-installer 下载模型")

# ── 隐藏导入（动态加载的模块） ──
hiddenimports = [
    # Textual 内部模块
    "textual.widgets",
    "textual.containers",
    "textual.screen",
    "textual.css",
    "textual.binding",
    "textual.keys",
    "textual._xterm_parser",
    # onnxruntime 原生后端
    "onnxruntime.capi",
    "onnxruntime.capi.onnxruntime_pybind11_state",
    # pystray 平台后端
    "pystray._win32",
    "pystray._darwin",
    "pystray._xorg",
    "pystray._gtk",
    "pystray._appindicator",
    "pystray._util",
    # Pillow
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageGrab",
    # Pygments 词法分析器（动态加载）
    "pygments.lexers",
    "pygments.lexers.python",
    "pygments.lexers.markup",
    "pygments.lexers.shell",
    "pygments.lexers.javascript",
    "pygments.lexers.json",
    "pygments.formatters",
    "pygments.styles",
    # httpx
    "httpcore",
    # ddgs
    "ddgs",
    # asyncio
    "asyncio",
    "multiprocessing",
]

# ── 排除的模块 ──
excludes = [
    "tkinter",
    "unittest",
    "pdb",
    "distutils",
    "setuptools",
    "pip",
    "wheel",
]

# ── PyInstaller Analysis ──
a = Analysis(
    ["shell/main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Aide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,       # Textual TUI 需要终端
    icon=None,
)

# ── onedir 输出 ──
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Aide",
)
