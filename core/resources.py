"""资源路径工具 — 兼容开发模式和 PyInstaller bundle 模式。

所有需要定位打包资源文件的模块都应使用 get_resource_path()，
而不是 Path(__file__).parent 的相对路径。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_bundled() -> bool:
    """是否运行在 PyInstaller bundle 中。

    PyInstaller 启动时设置 sys.frozen = True 和 sys._MEIPASS。
    """
    return getattr(sys, "frozen", False)


def get_bundle_dir() -> Path:
    """返回 bundle 根目录。

    - 开发模式: 项目根目录（从 core/resources.py 向上两级）
    - Bundle 模式: sys._MEIPASS（PyInstaller 临时解压目录）
    """
    if is_bundled():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # 开发模式：core/resources.py → core/ → 项目根
    return Path(__file__).parent.parent.resolve()


def get_resource_path(relative_path: str | Path) -> Path:
    """将相对路径解析为绝对路径。

    在开发模式下相对于项目根解析，
    在 bundle 模式下相对于 sys._MEIPASS 解析。

    用法:
        templates = get_resource_path("core/plugins/templates")
        css = get_resource_path("ui/textual_app/app.tcss")
        model = get_resource_path("models/all-MiniLM-L6-v2/model.onnx")
    """
    return (get_bundle_dir() / relative_path).resolve()
