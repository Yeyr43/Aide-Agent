#!/usr/bin/env bash
# macOS 平台验证脚本 — 运行所有关键检查
set -e

echo "=== Aide Agent — macOS 平台验证 ==="
echo ""

# 1. Python 版本
echo "--- Python 版本 ---"
python3 --version

# 2. 核心模块导入
echo ""
echo "--- 核心模块导入 ---"
python3 -c "from core.setup import aide_dir; print(f'AIDE_HOME: {aide_dir()}')"
python3 -c "from ui.textual_app.platform import IS_MACOS, can_use_tray; print(f'macOS: {IS_MACOS}, Tray: {can_use_tray()}')"

# 3. PyObjC 托盘依赖
echo ""
echo "--- 托盘依赖检查 ---"
python3 -c "
try:
    import pystray
    print('pystray: OK')
except ImportError:
    print('pystray: MISSING (pip install pystray)')
"
python3 -c "
try:
    import Quartz
    print('PyObjC Quartz: OK')
except ImportError:
    print('PyObjC Quartz: MISSING (uv sync --extra macos)')
"

# 4. ONNX/Embedding
echo ""
echo "--- Embedding 引擎 ---"
python3 -c "
try:
    from core.context.embeddings import get_embedding_engine
    engine = get_embedding_engine()
    if engine:
        print('Embedding engine: OK')
    else:
        print('Embedding engine: DISABLED (model not downloaded)')
except Exception as e:
    print(f'Embedding engine: ERROR ({e})')
"

# 5. 运行关键测试
echo ""
echo "--- 测试: 平台检测 + 配置 + AIDE_HOME ---"
python3 -m pytest tests/test_platform.py tests/test_setup.py tests/test_aide_home.py -v

echo ""
echo "=== 验证完成 ==="
