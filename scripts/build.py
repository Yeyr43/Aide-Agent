"""Aide 独立分发构建脚本。

用法:
    uv run python scripts/build.py              # 完整构建（下载模型 + PyInstaller）
    uv run python scripts/build.py --no-model   # 跳过模型下载（模型已存在时）
    uv run python scripts/build.py --no-installer # 仅下载模型（CI 两步构建）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib import request

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DIST_DIR = PROJECT_ROOT / "dist"
MODEL_DIR = PROJECT_ROOT / "models" / "all-MiniLM-L6-v2"
MODEL_URL = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx"
VOCAB_URL = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/vocab.txt"


def download_model() -> None:
    """下载 ONNX 模型和词表到 models/ 目录。"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model_path = MODEL_DIR / "model.onnx"
    vocab_path = MODEL_DIR / "vocab.txt"

    if not model_path.exists():
        print(f"下载 ONNX 模型: {MODEL_URL}")
        req = request.Request(MODEL_URL, headers={"User-Agent": "Aide-Build/0.1"})
        with request.urlopen(req, timeout=300) as resp:
            model_path.write_bytes(resp.read())
        size_kb = model_path.stat().st_size / 1024
        print(f"  -> {model_path} ({size_kb:.0f} KB)")

    if not vocab_path.exists():
        print(f"下载 vocab.txt: {VOCAB_URL}")
        req = request.Request(VOCAB_URL, headers={"User-Agent": "Aide-Build/0.1"})
        with request.urlopen(req, timeout=120) as resp:
            vocab_path.write_bytes(resp.read())
        size_kb = vocab_path.stat().st_size / 1024
        print(f"  -> {vocab_path} ({size_kb:.0f} KB)")

    print("模型下载完成。")


def run_pyinstaller() -> None:
    """运行 PyInstaller 构建。"""
    spec = PROJECT_ROOT / "Aide.spec"

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec)]
    print(f"运行: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)


def verify_output() -> None:
    """验证构建产物完整性。"""
    dist_dir = DIST_DIR / "Aide"
    exe = dist_dir / "Aide.exe" if sys.platform == "win32" else dist_dir / "Aide"

    if not exe.exists():
        print(f"错误: 未找到可执行文件: {exe}")
        sys.exit(1)

    print(f"可执行文件: {exe} ({exe.stat().st_size / 1024 / 1024:.0f} MB)")

    # 检查内部数据文件
    internal = dist_dir / "_internal"
    checks = {
        "ONNX 模型": internal / "models" / "all-MiniLM-L6-v2" / "model.onnx",
        "CSS": internal / "ui" / "textual_app" / "app.tcss",
        "插件模板": internal / "core" / "plugins" / "templates" / "hello-plugin",
        "MCP 配置": internal / "mcp" / "servers.json",
    }

    for label, path in checks.items():
        status = "OK" if path.exists() else "缺失"
        print(f"  {label}: {status}")

    # 总大小
    total_mb = sum(f.stat().st_size for f in dist_dir.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"总大小: {total_mb:.0f} MB")
    print(f"\n构建完成: {dist_dir}")


def copy_launchers() -> None:
    """Copy launcher scripts to dist directory."""
    import shutil

    dist_dir = DIST_DIR / "Aide"

    # Copy launcher scripts from project root
    for name in ("aide.ps1", "aide"):
        src = PROJECT_ROOT / name
        if src.exists():
            shutil.copy2(src, dist_dir / name)
            print(f"  Copied launcher: {name}")

    # Windows: create aide.bat wrapper
    bat = dist_dir / "aide.bat"
    bat.write_text(
        '@echo off\r\n'
        'powershell -ExecutionPolicy Bypass -File "%~dp0aide.ps1" %*\r\n',
        encoding="ascii",
    )
    print("  Created launcher: aide.bat")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 Aide 独立分发包")
    parser.add_argument("--no-model", action="store_true", help="跳过模型下载")
    parser.add_argument("--no-installer", action="store_true", help="仅下载模型（不运行 PyInstaller）")
    args = parser.parse_args()

    # CI 两步模式：仅下载模型
    if args.no_installer:
        download_model()
        print("模型已准备就绪，退出（--no-installer）。")
        return

    # 检查 PyInstaller 是否安装
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller 未安装。运行: uv sync (已包含 pyinstaller 依赖)")
        sys.exit(1)

    if not args.no_model:
        download_model()
    elif not (MODEL_DIR / "model.onnx").exists():
        print(f"错误: --no-model 但模型文件不存在: {MODEL_DIR / 'model.onnx'}")
        print("请先运行: uv run python scripts/build.py --no-installer")
        sys.exit(1)

    run_pyinstaller()
    verify_output()
    copy_launchers()


if __name__ == "__main__":
    main()
