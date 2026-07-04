# P4 Batch 1 — 架构升级 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Aide 从单体应用升级为可扩展平台 — 新增插件系统、分离 Kernel/UI、迁移配置到 ~/.aide/、重组目录结构。

**Architecture:** 五步渐进重构。每一步结束保证 63 测试通过 + 应用可运行。旧代码通过 shim 过渡，最后一步统一清理。插件系统采用三层架构（Contract → Runtime → SDK），兼容 Openclaw manifest 格式，支持热插拔。

**Tech Stack:** Python 3.13, Textual 0.80+, asyncio, dataclass, JSON 文件存储, importlib

## Global Constraints

- Python >= 3.13
- Textual >= 0.80
- `core/` 任何模块不得 `import textual` — CI 强制
- 每个子包 `__init__.py` 只 re-export 3~5 个公开符号
- AgentKernel 每个公开方法 ≤ 10 行
- 所有测试保持通过（当前 63 个，逐步新增）
- 配置不在项目目录中 — 在 `~/.aide/config/`
- 会话数据格式不变 — `meta.json`, `timeline.json`, `cache.json`, `overview.json`, `messages/turn_NNN.json`

---

### Task 1: Config — 创建 Config dataclass

**Files:**
- Create: `core/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: 无（纯地基）
- Produces: `Config`, `LLMConfig`, `AppConfig` dataclasses; `Config.load(cli_args)` classmethod

- [ ] **Step 1: 写 Config dataclass**

```python
# core/config.py
"""Aide 配置 — dataclass + 分层加载。

优先级: cli_args > 环境变量 (AIDE_*) > ~/.aide/config/settings.json > defaults.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096


@dataclass
class AppConfig:
    max_turns: int = 5
    window_turns: int = 8
    relevance_threshold: float = 0.15


DEFAULT_LLM = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "temperature": 0.7,
    "max_tokens": 4096,
}

DEFAULT_APP = {
    "max_turns": 5,
    "window_turns": 8,
    "relevance_threshold": 0.15,
}


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    app: AppConfig = field(default_factory=AppConfig)
    aide_root: Path = field(default_factory=lambda: Path.home() / ".aide")

    @classmethod
    def load(cls, cli_args: dict | None = None) -> "Config":
        """分层加载配置。

        Args:
            cli_args: 命令行参数 dict，如 {"model": "gpt-4o"}

        Returns:
            Config 实例
        """
        aide_root = Path.home() / ".aide"

        # 1. defaults.json（内置默认值）
        llm_data: dict = dict(DEFAULT_LLM)
        app_data: dict = dict(DEFAULT_APP)

        # 2. ~/.aide/config/settings.json
        settings_path = aide_root / "config" / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    user_settings = json.load(f)
                if "llm" in user_settings:
                    llm_data.update(user_settings["llm"])
                if "app" in user_settings:
                    app_data.update(user_settings["app"])
            except (json.JSONDecodeError, OSError):
                pass

        # 3. 环境变量 (AIDE_*)
        env_map = {
            "AIDE_PROVIDER": ("llm", "provider"),
            "AIDE_MODEL": ("llm", "model"),
            "AIDE_BASE_URL": ("llm", "base_url"),
            "AIDE_API_KEY": ("llm", "api_key"),
        }
        for env_var, (section, key) in env_map.items():
            val = os.environ.get(env_var, "")
            if val:
                if section == "llm":
                    llm_data[key] = val

        # 4. 命令行参数
        cli_args = cli_args or {}
        llm_cli_keys = {"provider", "model", "base_url", "api_key"}
        for key in llm_cli_keys:
            if key in cli_args:
                llm_data[key] = cli_args[key]

        return cls(
            llm=LLMConfig(**llm_data),
            app=AppConfig(**app_data),
            aide_root=aide_root,
        )

    @property
    def sessions_root(self) -> Path:
        return self.aide_root / "sessions"

    @property
    def plugins_dir(self) -> Path:
        return self.aide_root / "plugins"

    @property
    def agent_dir(self) -> Path:
        return self.aide_root / "agent"

    @property
    def backups_dir(self) -> Path:
        return self.aide_root / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.aide_root / "logs"
```

- [ ] **Step 2: 写测试**

```python
# tests/test_config.py
import json
import os
from pathlib import Path
from core.config import Config, LLMConfig, AppConfig


class TestConfigDefaults:
    def test_default_llm_provider(self):
        config = Config()
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4o-mini"

    def test_default_app_settings(self):
        config = Config()
        assert config.app.max_turns == 5
        assert config.app.window_turns == 8

    def test_default_aide_root(self):
        config = Config()
        assert config.aide_root == Path.home() / ".aide"

    def test_default_properties(self):
        config = Config()
        assert config.sessions_root == Path.home() / ".aide" / "sessions"
        assert config.plugins_dir == Path.home() / ".aide" / "plugins"


class TestConfigLoad:
    def test_load_from_settings_json(self, tmp_path):
        aide_root = tmp_path / ".aide"
        config_dir = aide_root / "config"
        config_dir.mkdir(parents=True)
        settings = {
            "llm": {"provider": "ollama", "model": "llama3"},
            "app": {"max_turns": 10},
        }
        (config_dir / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )

        with _patch_aide_root(aide_root):
            config = Config.load()
        assert config.llm.provider == "ollama"
        assert config.llm.model == "llama3"
        assert config.app.max_turns == 10

    def test_env_override(self, tmp_path, monkeypatch):
        aide_root = tmp_path / ".aide"
        (aide_root / "config").mkdir(parents=True)

        monkeypatch.setenv("AIDE_MODEL", "gpt-4o")
        monkeypatch.setenv("AIDE_PROVIDER", "openai")

        with _patch_aide_root(aide_root):
            config = Config.load()
        assert config.llm.model == "gpt-4o"

    def test_cli_override_takes_highest_priority(self, tmp_path, monkeypatch):
        aide_root = tmp_path / ".aide"
        (aide_root / "config").mkdir(parents=True)

        monkeypatch.setenv("AIDE_MODEL", "env-model")

        with _patch_aide_root(aide_root):
            config = Config.load(cli_args={"model": "cli-model"})
        assert config.llm.model == "cli-model"


def _patch_aide_root(path: Path):
    """Context manager: 临时替换 Path.home() 使 aide_root 指向 tmp_path。"""
    import contextlib
    import unittest.mock
    return unittest.mock.patch(
        "core.config.Path.home", return_value=path
    )
```

- [ ] **Step 3: 运行测试验证**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 7 tests PASS

- [ ] **Step 4: 确保 ~/.aide/config/ 目录结构**

更新 `core/bootstrap.py` 的 `_ensure_dirs()` 加入 `config/` 和 `backups/` 目录：

```python
# core/bootstrap.py — 修改 _ensure_dirs
def _ensure_dirs(aide: Path) -> None:
    dirs = [
        aide / "agent" / "data",
        aide / "config",          # 新
        aide / "sessions",
        aide / "plugins",
        aide / "backups",         # 新
        aide / "logs",
        aide / "archives",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
```

同时在 `ensure_aide_root()` 末尾加写入 `defaults.json`：

```python
# core/bootstrap.py — ensure_aide_root() 末尾追加
config_dir = aide / "config"
defaults_path = config_dir / "defaults.json"
if not defaults_path.exists():
    default_config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        "app": {
            "max_turns": 5,
            "window_turns": 8,
            "relevance_threshold": 0.15,
        },
    }
    defaults_path.write_text(
        json.dumps(default_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

- [ ] **Step 5: 运行全量测试验证**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add core/config.py tests/test_config.py core/bootstrap.py
git commit -m "feat(p4): add Config dataclass with layered loading (defaults -> settings.json -> env -> cli)"
```

---

### Task 2: Config — 旧 settings.py shim + llm_gateway 适配

**Files:**
- Modify: `config/settings.py`
- Modify: `core/llm_gateway/__init__.py`
- Modify: `shell/main.py`

**Interfaces:**
- Consumes: `core/config.py` (Task 1)
- Produces: `config/settings.py` shim 保持向后兼容; `create_provider()` 接受 `LLMConfig` dataclass

- [ ] **Step 1: 设 shim — config/settings.py 转发到新 Config**

```python
# config/settings.py（替换现有内容）
"""向后兼容 shim — 转发到 core.config。

P4: 配置已迁移到 core/config.py + ~/.aide/config/。
此文件保留以支持过渡期的旧 import 路径。
"""
from core.config import Config, LLMConfig, AppConfig

# 保持旧模块的 Settings 类可用
class Settings:
    """deprecated: 请改用 core.config.Config"""
    def __init__(self, config_path=None) -> None:
        from pathlib import Path
        config = Config.load()
        self.llm = LLMConfig(
            provider=config.llm.provider,
            model=config.llm.model,
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
        )
```

- [ ] **Step 2: 适配 llm_gateway — create_provider 接受 dataclass**

```python
# core/llm_gateway/__init__.py — 修改 create_provider 签名
from core.config import LLMConfig  # 新

def create_provider(config: LLMConfig):
    """根据配置创建对应的 LLM Provider 实例。

    Args:
        config: core.config.LLMConfig dataclass

    Returns:
        OpenAIProvider 或 OllamaProvider 实例
    """
    if config.provider == "openai":
        return OpenAIProvider(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
        )
    elif config.provider == "ollama":
        return OllamaProvider(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
        )
    else:
        raise ValueError(
            f"不支持的 LLM provider: {config.provider}\n"
            f"P1 支持: openai, ollama"
        )
```

旧的 `from config.settings import LLMConfig` import 也改为 `from core.config import LLMConfig`：

```python
# core/llm_gateway/__init__.py — 修改 import 行
from core.config import LLMConfig  # 曾: from config.settings import LLMConfig
```

删除旧 import：
```python
# 删除这行
from config.settings import LLMConfig
```

- [ ] **Step 3: 适配 shell/main.py — 使用新 Config**

在 `shell/main.py` 中用新 `Config.load()` 替代 `Settings()` 加载：

```python
# shell/main.py — main() 中的 Settings 替换
# 旧: from config.settings import Settings
# 新: from core.config import Config

def main() -> None:
    ensure_aide_root()
    migrate_config()
    # 迁移旧 config.json → settings.json（如果存在）
    _migrate_old_config()
    app = AideApp()
    app.run()

def _migrate_old_config() -> None:
    """迁移逻辑：如果 ~/.aide/agent/config.json 存在但 ~/.aide/config/settings.json 不存在，
    则自动迁移。"""
    import json, shutil
    old_config = Path.home() / ".aide" / "agent" / "config.json"
    new_settings = Path.home() / ".aide" / "config" / "settings.json"
    if old_config.exists() and not new_settings.exists():
        shutil.copy2(old_config, new_settings)
```

- [ ] **Step 4: 运行全量测试**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS

- [ ] **Step 5: 验证应用可启动**

```bash
uv run python shell/main.py
```
Expected: 正常启动到首页。Ctrl+C 退出。

- [ ] **Step 6: Commit**

```bash
git add config/settings.py core/llm_gateway/__init__.py shell/main.py
git commit -m "feat(p4): add config shim, adapt llm_gateway and main to new Config"
```

---

### Task 3: Storage — JsonStore 接受 base_dir 参数

**Files:**
- Modify: `core/storage.py`
- Modify: `tests/test_context_manager.py` (如果调用构造方式变化)

**Interfaces:**
- Consumes: `core/config.py` (Task 1)
- Produces: `JsonStore(base_dir: Path = Path.home() / ".aide")`

- [ ] **Step 1: 修改 JsonStore 构造函数**

读取 [core/storage.py](core/storage.py) 的 `JsonStore.__init__`，加 `base_dir` 参数：

```python
# core/storage.py — JsonStore 类
class JsonStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or Path.home() / ".aide"
        self._lock = asyncio.Lock()
        # ... 其余初始化逻辑不变
```

`read()` 和 `write()` 中如有硬编码路径，替换为 `self._base_dir / relative_path`。

- [ ] **Step 2: 更新调用方**

`app.py` 中 `JsonStore()` 调用不变（默认参数向后兼容）。
`test_context_manager.py` 中 `JsonStore()` 调用不变。

- [ ] **Step 3: 运行全量测试**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS

- [ ] **Step 4: Commit**

```bash
git add core/storage.py
git commit -m "feat(p4): JsonStore accepts base_dir parameter"
```

---

### Task 4: Step 2 开头 — 创建新目录结构

**Files:**
- Create: `core/kernel/__init__.py`
- Create: `core/context/__init__.py`
- Create: `core/tools/builtin/__init__.py`
- Create: `core/commands/__init__.py`
- Create: `core/commands/builtin/__init__.py`
- Create: `core/plugins/__init__.py`
- Create: `core/memory/__init__.py`
- Create: `core/sessions/__init__.py`
- Create: `core/tools/mcp/__init__.py`

**Interfaces:**
- Consumes: 无
- Produces: 空包目录，__init__.py 含 docstring

- [ ] **Step 1: 创建所有新目录和 __init__.py**

```bash
mkdir -p d:/SEAI/Aide/core/kernel
mkdir -p d:/SEAI/Aide/core/context
mkdir -p d:/SEAI/Aide/core/tools/builtin
mkdir -p d:/SEAI/Aide/core/tools/mcp
mkdir -p d:/SEAI/Aide/core/commands/builtin
mkdir -p d:/SEAI/Aide/core/plugins
mkdir -p d:/SEAI/Aide/core/memory
mkdir -p d:/SEAI/Aide/core/sessions
mkdir -p d:/SEAI/Aide/tests/kernel
mkdir -p d:/SEAI/Aide/tests/plugins
mkdir -p d:/SEAI/Aide/tests/commands
```

- [ ] **Step 2: 给每个新包写带 docstring 的 __init__.py**

为 `core/kernel/__init__.py`, `core/context/__init__.py`, `core/tools/builtin/__init__.py`, `core/tools/mcp/__init__.py`, `core/commands/__init__.py`, `core/commands/builtin/__init__.py`, `core/plugins/__init__.py`, `core/memory/__init__.py`, `core/sessions/__init__.py` 分别创建空 `__init__.py`，含一行 docstring。

例如 `core/kernel/__init__.py`：
```python
"""Kernel — Agent 内核: AgentKernel 门面 + FC 循环 + 状态机。"""
```

- [ ] **Step 3: Commit**

```bash
git add core/kernel/ core/context/ core/tools/builtin/ core/tools/mcp/ core/commands/ core/plugins/ core/memory/ core/sessions/ tests/kernel/ tests/plugins/ tests/commands/
git commit -m "feat(p4): scaffold new directory structure for kernel/context/plugins/memory/sessions/commands"
```

---

### Task 5: Step 2 续 — 移动 executor 到 kernel

**Files:**
- Move: `core/executor/loop.py` → `core/kernel/fc_loop.py`
- Move: `core/executor/state.py` → `core/kernel/state.py`
- Modify: `core/executor/__init__.py` (变成 shim)
- Create: `core/kernel/__init__.py` (更新 re-export)

**Interfaces:**
- Consumes: `core/llm_gateway`, `core/tools`
- Produces: `core.kernel.FunctionCallingLoop`, `core.kernel.ExecutorUI`, `core.kernel.ExecutorState`

- [ ] **Step 1: 复制（不删除）文件**

```bash
cp d:/SEAI/Aide/core/executor/loop.py d:/SEAI/Aide/core/kernel/fc_loop.py
cp d:/SEAI/Aide/core/executor/state.py d:/SEAI/Aide/core/kernel/state.py
```

- [ ] **Step 2: 更新 fc_loop.py 的 import 路径**

修改 `core/kernel/fc_loop.py`，把相对 import 改为绝对 import：

```python
# core/kernel/fc_loop.py — 改 import
from .state import ExecutorState  # 不变，同目录
from core.tools import ToolRegistry  # 曾: from ..tools import ToolRegistry
from core.llm_gateway import TextDelta, StreamEnd  # 曾: from ..llm_gateway import TextDelta, StreamEnd
```

- [ ] **Step 3: 更新 kernel/__init__.py**

```python
# core/kernel/__init__.py
"""Kernel — Agent 内核: AgentKernel 门面 + FC 循环 + 状态机。"""

from .state import ExecutorState
from .fc_loop import FunctionCallingLoop, ExecutorUI

__all__ = ["FunctionCallingLoop", "ExecutorState", "ExecutorUI"]
```

- [ ] **Step 4: 设 shim — core/executor/__init__.py 转发**

```python
# core/executor/__init__.py（替换现有内容）
"""向后兼容 shim — P4 已移至 core.kernel。"""

from core.kernel import FunctionCallingLoop, ExecutorState, ExecutorUI

__all__ = ["FunctionCallingLoop", "ExecutorState", "ExecutorUI"]
```

- [ ] **Step 5: 运行全量测试验证 shim**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS

- [ ] **Step 6: Commit**

```bash
git add core/kernel/fc_loop.py core/kernel/state.py core/kernel/__init__.py core/executor/__init__.py
git commit -m "refactor(p4): move executor to kernel with shim"
```

---

### Task 6: Step 2 续 — 移动 context_manager 到 context

**Files:**
- Move: `core/context_manager/assembler.py` → `core/context/pipeline.py`
- Move: `core/context_manager/ingester.py` → `core/context/ingester.py`
- Move: `core/context_manager/compactor.py` → `core/context/compactor.py`
- Create: `core/context/relevance.py` (从 pipeline.py 提取 bigram + topic + decision 工具)
- Modify: `core/context/__init__.py`, `core/context_manager/__init__.py`

**Interfaces:**
- Consumes: `core/storage`
- Produces: `core.context.ContextPipeline` (曾 ContextAssembler), `core.context.ContextIngester`, `core.context.ContextCompactor`

- [ ] **Step 1: 复制文件到新位置**

```bash
cp d:/SEAI/Aide/core/context_manager/assembler.py d:/SEAI/Aide/core/context/pipeline.py
cp d:/SEAI/Aide/core/context_manager/ingester.py d:/SEAI/Aide/core/context/ingester.py
cp d:/SEAI/Aide/core/context_manager/compactor.py d:/SEAI/Aide/core/context/compactor.py
```

- [ ] **Step 2: 从 pipeline.py 提取 relevance.py**

新建 `core/context/relevance.py`，包含 `_bigrams`, `_jaccard`, `_extract_topics`, `_extract_decisions`, `_build_overview`, `_split_conversation`, `WINDOW_TURNS`, `DECISION_KEYWORDS`, `STOP_WORDS`：

```python
# core/context/relevance.py
"""纯函数工具：bigram Jaccard、话题提取、决策检测、对话切分、历史总览。"""

import re
from collections import Counter
from pathlib import Path

WINDOW_TURNS = 8

DECISION_KEYWORDS = re.compile(
    r'(确定|决定|选择|采用|最终|结论是|方案是|就用|还是用|'
    r'建议|推荐|修改了|创建了|删除了|更新了)'
)

STOP_WORDS = frozenset({
    '这个', '那个', '什么', '怎么', '为什么', '可以', '能不能',
    '帮我', '一个', '一下', '一些', '这些', '那些',
    '有没有', '是不是', '能不能', '可不可以', '我需要', '我想要',
    '请问', '麻烦', '然后', '所以', '但是', '因为', '如果', '虽然',
    '我们', '你们', '他们', '哪里',
    '编写', '现在', '知道',
})


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _extract_topics(text: str, max_topics: int = 3) -> list[str]:
    # （从 pipeline.py 复制完整实现）
    ...


def _extract_decisions(text: str) -> list[str]:
    # （从 pipeline.py 复制完整实现）
    ...


def _build_overview(
    session_dir: Path | None,
    older_conversation: list[dict],
    older_cache_entries: list[dict] | None = None,
) -> str:
    # （从 pipeline.py 复制完整实现）
    ...


def _split_conversation(
    conversation: list[dict],
    window: int = WINDOW_TURNS,
) -> tuple[list[dict], list[dict]]:
    # （从 pipeline.py 复制完整实现）
    ...
```

然后更新 `core/context/pipeline.py`，从 `relevance.py` import：

```python
# core/context/pipeline.py — 顶部
from .relevance import (
    _bigrams, _jaccard, _extract_topics, _extract_decisions,
    _build_overview, _split_conversation, WINDOW_TURNS,
)

# 删除 pipeline.py 中原有的这些函数定义
```

`ContextAssembler` 类重命名为 `ContextPipeline`：

```python
# core/context/pipeline.py
class ContextPipeline:  # 曾: ContextAssembler
    """组装上下文，支持 8 轮窗口 + 早期轮次总览。"""
    ...
```

- [ ] **Step 3: 更新 context/__init__.py**

```python
# core/context/__init__.py
"""Context — 上下文管线: Soul→Prompt→Overview→Cache。"""

from .ingester import ContextIngester
from .pipeline import ContextPipeline
from .compactor import ContextCompactor

__all__ = ["ContextIngester", "ContextPipeline", "ContextCompactor"]
```

- [ ] **Step 4: 设 shim — context_manager/__init__.py**

```python
# core/context_manager/__init__.py（替换现有内容）
"""向后兼容 shim — P4 已移至 core.context。"""

from core.context import ContextIngester, ContextPipeline, ContextCompactor

# 别名保持完全向后兼容
ContextAssembler = ContextPipeline

__all__ = ["ContextIngester", "ContextAssembler", "ContextPipeline", "ContextCompactor"]
```

- [ ] **Step 5: 更新 app.py 的 import**

`app.py` 中 `from core.context_manager.assembler import _split_conversation` → `from core.context.relevance import _split_conversation`

```python
# ui/textual_app/app.py — 修改 import
from core.context.relevance import _split_conversation
# 删除: from core.context_manager.assembler import _split_conversation
```

- [ ] **Step 6: 运行全量测试**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS

- [ ] **Step 7: Commit**

```bash
git add core/context/ core/context_manager/__init__.py ui/textual_app/app.py
git commit -m "refactor(p4): move context_manager to context, extract relevance utils"
```

---

### Task 7: Step 2 续 — 移动 prompt_manager 到 memory

**Files:**
- Move: `core/prompt_manager/capture.py` → `core/memory/capture.py`
- Move: `core/prompt_manager/entries.py` → `core/memory/entries.py`
- Move: `core/prompt_manager/updater.py` → `core/memory/updater.py`
- Move: `core/prompt_manager/topic_tracker.py` → `core/memory/tracker.py`
- Modify: `core/memory/__init__.py`, `core/prompt_manager/__init__.py`

**Interfaces:**
- Consumes: `core/storage`
- Produces: `core.memory.CaptureEngine`, `core.memory.EntryManager`, `core.memory.PromptUpdater`, `core.memory.TopicFrequencyTracker`

- [ ] **Step 1: 复制文件**

```bash
cp d:/SEAI/Aide/core/prompt_manager/capture.py d:/SEAI/Aide/core/memory/capture.py
cp d:/SEAI/Aide/core/prompt_manager/entries.py d:/SEAI/Aide/core/memory/entries.py
cp d:/SEAI/Aide/core/prompt_manager/updater.py d:/SEAI/Aide/core/memory/updater.py
cp d:/SEAI/Aide/core/prompt_manager/topic_tracker.py d:/SEAI/Aide/core/memory/tracker.py
```

- [ ] **Step 2: 更新各文件内部 import**

`core/memory/entries.py`：`from ..storage import JsonStore` → `from core.storage import JsonStore`
`core/memory/capture.py`：`from .entries import EntryManager` + `from .tracker import TopicFrequencyTracker`（同目录不变）
`core/memory/updater.py`：`from .entries import EntryManager` + LLM gateway import 改为 `from core.llm_gateway import TextDelta, StreamEnd`
`core/memory/tracker.py`：`from ..storage import JsonStore` → `from core.storage import JsonStore`

- [ ] **Step 3: 更新 memory/__init__.py**

```python
# core/memory/__init__.py
"""Memory — 记忆系统: CaptureEngine + EntryManager + PromptUpdater + TopicFrequencyTracker。"""

from .capture import CaptureEngine
from .entries import EntryManager
from .updater import PromptUpdater
from .tracker import TopicFrequencyTracker

__all__ = [
    "CaptureEngine",
    "EntryManager",
    "PromptUpdater",
    "TopicFrequencyTracker",
]
```

- [ ] **Step 4: 设 shim — prompt_manager/__init__.py**

```python
# core/prompt_manager/__init__.py（替换现有内容）
"""向后兼容 shim — P4 已移至 core.memory。"""

from core.memory import (
    CaptureEngine, EntryManager, PromptUpdater, TopicFrequencyTracker,
)

__all__ = [
    "CaptureEngine",
    "EntryManager",
    "PromptUpdater",
    "TopicFrequencyTracker",
]
```

- [ ] **Step 5: 运行全量测试**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS

- [ ] **Step 6: Commit**

```bash
git add core/memory/ core/prompt_manager/__init__.py
git commit -m "refactor(p4): move prompt_manager to memory with shim"
```

---

### Task 8: Step 2 续 — 移动工具文件到 builtin/

**Files:**
- Move: `core/tools/read_file.py` → `core/tools/builtin/read_file.py`
- Move: `core/tools/write_file.py` → `core/tools/builtin/write_file.py`
- Move: `core/tools/run_shell.py` → `core/tools/builtin/run_shell.py`
- Move: `core/tools/search_memory.py` → `core/tools/builtin/search_memory.py`
- Move: `core/tools/web_search.py` → `core/tools/builtin/web_search.py`
- Move: `core/tools/plugin_protocol.py` → `core/tools/protocol.py`
- Modify: `core/tools/__init__.py`

**Interfaces:**
- Consumes: 现有工具文件
- Produces: `core.tools.builtin.*` 子包，`core.tools.protocol` 升级

- [ ] **Step 1: 移动文件**

```bash
cp d:/SEAI/Aide/core/tools/read_file.py d:/SEAI/Aide/core/tools/builtin/read_file.py
cp d:/SEAI/Aide/core/tools/write_file.py d:/SEAI/Aide/core/tools/builtin/write_file.py
cp d:/SEAI/Aide/core/tools/run_shell.py d:/SEAI/Aide/core/tools/builtin/run_shell.py
cp d:/SEAI/Aide/core/tools/search_memory.py d:/SEAI/Aide/core/tools/builtin/search_memory.py
cp d:/SEAI/Aide/core/tools/web_search.py d:/SEAI/Aide/core/tools/builtin/web_search.py
cp d:/SEAI/Aide/core/tools/plugin_protocol.py d:/SEAI/Aide/core/tools/protocol.py
```

- [ ] **Step 2: 更新 builtin/__init__.py**

```python
# core/tools/builtin/__init__.py
"""内置工具 — read_file, write_file, run_shell, search_memory, web_search。"""

from . import read_file, write_file, run_shell, search_memory, web_search

BUILTIN_TOOLS = [read_file, write_file, run_shell, search_memory, web_search]
```

- [ ] **Step 3: 更新 tools/__init__.py 的 import**

```python
# core/tools/__init__.py — 修改 import
from .builtin import read_file, write_file, run_shell, search_memory, web_search
from .protocol import ToolProtocol  # 新
```

- [ ] **Step 4: 运行全量测试**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS

- [ ] **Step 5: Commit**

```bash
git add core/tools/builtin/ core/tools/protocol.py core/tools/__init__.py
git commit -m "refactor(p4): move tools into builtin/ subpackage, rename plugin_protocol to protocol"
```

---

### Task 9: Step 2 续 — 移动指令到 core/commands

**Files:**
- Move: `ui/textual_app/commands/handlers.py` → `core/commands/builtin/handlers.py`
- Create: `core/commands/router.py`
- Create: `core/commands/__init__.py`
- Modify: `ui/textual_app/commands/__init__.py` (变成 shim)

**Interfaces:**
- Consumes: 现有命令处理器
- Produces: `core.commands.CommandRegistry`, `core.commands.route_command`

- [ ] **Step 1: 复制 handlers.py**

```bash
cp d:/SEAI/Aide/ui/textual_app/commands/handlers.py d:/SEAI/Aide/core/commands/builtin/handlers.py
```

- [ ] **Step 2: 创建 CommandRegistry 和 Router**

```python
# core/commands/__init__.py
"""命令系统 — CommandRegistry + CommandDefinition + 路由。"""

from dataclasses import dataclass, field
from typing import Callable, Awaitable

Handler = Callable[..., Awaitable[str]]


@dataclass
class CommandDefinition:
    name: str                # "/help"
    description: str         # "显示所有可用命令"
    handler: Handler         # async (app, args) -> str
    source: str = "builtin"  # "builtin" | "plugin:<id>"


class CommandRegistry:
    """指令注册中心。"""

    def __init__(self) -> None:
        self._commands: dict[str, CommandDefinition] = {}

    def register(self, cmd: CommandDefinition) -> None:
        self._commands[cmd.name] = cmd

    def unregister(self, name: str) -> bool:
        return self._commands.pop(name, None) is not None

    def unregister_source(self, source: str) -> int:
        removed = 0
        for name in list(self._commands):
            if self._commands[name].source == source:
                self._commands.pop(name)
                removed += 1
        return removed

    def get(self, name: str) -> CommandDefinition | None:
        return self._commands.get(name)

    def list_all(self) -> list[CommandDefinition]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def route(self, text: str) -> tuple[Handler, str] | None:
        """解析用户输入，匹配命令。"""
        text = text.strip()
        if not text.startswith("/") or text == "/":
            return None

        for cmd in sorted(self._commands, key=len, reverse=True):
            if text == cmd or text.startswith(cmd + " "):
                args = text[len(cmd):].strip()
                return (self._commands[cmd].handler, args)

        # 前缀匹配
        for cmd in sorted(self._commands, key=len, reverse=True):
            if cmd.startswith(text):
                args = text[len(cmd):].strip()
                return (self._commands[cmd].handler, args)

        # 模糊匹配
        user_cmd = text.split()[0]
        for cmd in sorted(self._commands, key=len, reverse=True):
            common = sum(1 for c1, c2 in zip(user_cmd, cmd) if c1 == c2)
            if common >= len(cmd) * 0.5:
                remaining = text[len(user_cmd):].strip()
                return (self._commands[cmd].handler, remaining)

        return None
```

- [ ] **Step 3: 更新 builtin/handlers.py — 适配新的 registry**

```python
# core/commands/builtin/handlers.py
# 改为从 core.commands 导入，去掉 Textual 依赖
# 命令注册改为类方法，支持传入 CommandRegistry

def register_builtin_commands(registry: CommandRegistry) -> None:
    """注册所有内置命令。"""
    from core.commands import CommandDefinition

    registry.register(CommandDefinition(
        name="/help", description="显示所有可用命令",
        handler=_handle_help,
    ))
    registry.register(CommandDefinition(
        name="/profile", description="查看当前 Soul + 动态 prompt",
        handler=_handle_profile,
    ))
    # ... 其余命令
```

- [ ] **Step 4: 更新 ui/commands/__init__.py shim**

```python
# ui/textual_app/commands/__init__.py（替换现有内容）
"""向后兼容 shim — P4 已移至 core.commands。"""
from core.commands.builtin.handlers import COMMANDS, route_command
__all__ = ["COMMANDS", "route_command"]
```

- [ ] **Step 5: 运行全量测试**

```bash
uv run pytest tests/ -q
```

Expected: 63 PASS

- [ ] **Step 6: Commit**

```bash
git add core/commands/ ui/textual_app/commands/__init__.py
git commit -m "refactor(p4): move commands to core/commands, add CommandRegistry + Router"
```

---

### Task 10: Step 3 — 插件系统: Contract 层

**Files:**
- Create: `core/plugins/contract.py`
- Create: `tests/plugins/test_contract.py`

**Interfaces:**
- Consumes: `core/tools` (ToolDefinition), `core/commands` (CommandDefinition)
- Produces: `PluginManifest`, `PluginAPI`, `PluginSlot`, `ContextProvider` (Protocol)

- [ ] **Step 1: 写 contract.py**

```python
# core/plugins/contract.py
"""Plugin contract — manifest model, PluginAPI, PluginSlot, ContextProvider."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from core.tools import ToolDefinition
from core.commands import CommandDefinition


@dataclass
class PluginManifest:
    """插件 manifest — Openclaw 兼容字段 + Aide 扩展。"""

    id: str
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    kind: str = "composite"          # "tool" | "command" | "provider" | "composite"
    entry: str = "__init__.py"       # Python 入口模块
    config_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    requires: dict = field(default_factory=dict)   # {"aide": ">=0.4.0"}
    slots: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    root_dir: Path = field(default_factory=Path)    # 插件根目录

    @classmethod
    def from_dir(cls, plugin_dir: Path) -> "PluginManifest | None":
        """从目录加载 manifest（优先级：aide.plugin.json > openclaw.plugin.json）。"""
        for fname in ["aide.plugin.json", "openclaw.plugin.json"]:
            path = plugin_dir / fname
            if path.exists():
                import json
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return cls(
                        id=data.get("id", plugin_dir.name),
                        name=data.get("name", data.get("id", plugin_dir.name)),
                        version=data.get("version", "0.1.0"),
                        description=data.get("description", ""),
                        kind=data.get("kind", "composite"),
                        entry=data.get("entry", "__init__.py"),
                        config_schema=data.get("configSchema", {"type": "object", "properties": {}}),
                        requires=data.get("requires", {}),
                        slots=data.get("slots", []),
                        provides=data.get("provides", []),
                        root_dir=plugin_dir,
                    )
                except (json.JSONDecodeError, OSError):
                    return None
        return None


@runtime_checkable
class ContextProvider(Protocol):
    """上下文提供者 Protocol — 插件可动态注入 system prompt。"""

    async def provide(self, user_msg: str, session_dir: Path | None) -> str:
        """返回要注入 system prompt 的文本。"""
        ...


@dataclass
class PluginSlot:
    """扩展点定义 — 一个命名的能力槽位。"""
    name: str
    description: str = ""
    filled_by: str | None = None  # plugin_id
    implementation: object = None
```

- [ ] **Step 2: 写 PluginAPI**

```python
# core/plugins/contract.py — 追加

class PluginAPI:
    """插件运行时 API — 在 register(api) 中暴露给插件。"""

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._tools: list[ToolDefinition] = []
        self._commands: list[CommandDefinition] = []
        self._context_providers: list[ContextProvider] = []
        self._filled_slots: list[str] = []
        self._provided_slots: list[str] = []
        self._startup_hooks: list[Callable] = []
        self._shutdown_hooks: list[Callable] = []

    def register_tool(self, tool: ToolDefinition) -> None:
        self._tools.append(tool)

    def register_command(self, cmd: CommandDefinition) -> None:
        cmd.source = f"plugin:{self._plugin_id}"
        self._commands.append(cmd)

    def register_context_provider(self, provider: ContextProvider) -> None:
        self._context_providers.append(provider)

    def fill_slot(self, slot_name: str, implementation: object) -> None:
        self._filled_slots.append(slot_name)
        # implementation 挂到 slot 上，供 PluginHost 匹配

    def provide_slot(self, slot_name: str) -> None:
        self._provided_slots.append(slot_name)

    def on_startup(self, callback: Callable[[], None]) -> None:
        self._startup_hooks.append(callback)

    def on_shutdown(self, callback: Callable[[], None]) -> None:
        self._shutdown_hooks.append(callback)
```

- [ ] **Step 3: 写测试**

```python
# tests/plugins/test_contract.py
import json
from pathlib import Path
from core.plugins.contract import PluginManifest, PluginAPI, PluginSlot


class TestPluginManifest:
    def test_from_dir_aide_manifest(self, tmp_path):
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        manifest = {
            "id": "my-plugin",
            "name": "My Plugin",
            "version": "1.0.0",
            "description": "Test plugin",
            "kind": "tool",
        }
        (plugin_dir / "aide.plugin.json").write_text(json.dumps(manifest))

        m = PluginManifest.from_dir(plugin_dir)
        assert m is not None
        assert m.id == "my-plugin"
        assert m.name == "My Plugin"
        assert m.kind == "tool"

    def test_from_dir_openclaw_manifest(self, tmp_path):
        plugin_dir = tmp_path / "oc-plugin"
        plugin_dir.mkdir()
        manifest = {"id": "oc-plugin", "kind": "composite"}
        (plugin_dir / "openclaw.plugin.json").write_text(json.dumps(manifest))

        m = PluginManifest.from_dir(plugin_dir)
        assert m is not None
        assert m.id == "oc-plugin"

    def test_from_dir_no_manifest(self, tmp_path):
        plugin_dir = tmp_path / "empty"
        plugin_dir.mkdir()
        assert PluginManifest.from_dir(plugin_dir) is None

    def test_aide_manifest_priority(self, tmp_path):
        plugin_dir = tmp_path / "dual"
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(json.dumps({"id": "aide-one"}))
        (plugin_dir / "openclaw.plugin.json").write_text(json.dumps({"id": "oc-one"}))

        m = PluginManifest.from_dir(plugin_dir)
        assert m is not None
        assert m.id == "aide-one"  # aide 优先


class TestPluginAPI:
    def test_register_tool(self):
        from core.tools import ToolDefinition
        api = PluginAPI("test")
        tool = ToolDefinition(name="test_tool", description="test", parameters={})
        api.register_tool(tool)
        assert api._tools == [tool]

    def test_register_command_sets_source(self):
        from core.commands import CommandDefinition
        api = PluginAPI("my-plugin")
        cmd = CommandDefinition(name="/test", description="test",
                               handler=lambda _: None)  # noqa
        api.register_command(cmd)
        assert cmd.source == "plugin:my-plugin"

    def test_startup_shutdown_hooks(self):
        api = PluginAPI("test")
        called = []
        api.on_startup(lambda: called.append("start"))
        api.on_shutdown(lambda: called.append("stop"))
        api._startup_hooks[0]()
        api._shutdown_hooks[0]()
        assert called == ["start", "stop"]
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/plugins/test_contract.py -v
```

Expected: ~6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/plugins/contract.py tests/plugins/test_contract.py
git commit -m "feat(p4): add plugin contract layer — Manifest, PluginAPI, PluginSlot, ContextProvider"
```

---

### Task 11: Step 3 — 插件系统: SDK + Host 层

**Files:**
- Create: `core/plugins/sdk.py`
- Create: `core/plugins/host.py`
- Create: `core/plugins/slots.py`
- Create: `tests/plugins/test_host.py`

**Interfaces:**
- Consumes: `core.plugins.contract` (Task 10), `core.tools.ToolRegistry`, `core.commands.CommandRegistry`
- Produces: `PluginHost`, `define_plugin()`, Slot 匹配逻辑

- [ ] **Step 1: 写 sdk.py**

```python
# core/plugins/sdk.py
"""Plugin SDK — define_plugin() 装饰器 + 对外 API surface。"""

from __future__ import annotations

from typing import Callable
from .contract import PluginAPI

PluginEntry = Callable[[PluginAPI], None]


def define_plugin(plugin_id: str) -> Callable[[PluginEntry], PluginEntry]:
    """装饰器：标记 Python 函数为插件入口。

    Usage:
        @define_plugin("my-plugin")
        def register(api: PluginAPI):
            api.register_tool(my_tool)
    """
    def decorator(fn: PluginEntry) -> PluginEntry:
        fn.__aide_plugin_id__ = plugin_id  # type: ignore[attr-defined]
        return fn
    return decorator
```

- [ ] **Step 2: 写 slots.py**

```python
# core/plugins/slots.py
"""Slot 系统 — 扩展点注册与匹配。"""

from .contract import PluginSlot


class SlotRegistry:
    """Slot 注册表。"""

    def __init__(self) -> None:
        self._slots: dict[str, PluginSlot] = {}

    def declare(self, name: str, description: str = "") -> PluginSlot:
        slot = self._slots.get(name, PluginSlot(name=name, description=description))
        if description and not slot.description:
            slot.description = description
        self._slots[name] = slot
        return slot

    def fill(self, name: str, plugin_id: str, implementation: object) -> bool:
        slot = self._slots.get(name)
        if slot is None:
            return False
        slot.filled_by = plugin_id
        slot.implementation = implementation
        return True

    def unfill(self, plugin_id: str) -> int:
        count = 0
        for slot in self._slots.values():
            if slot.filled_by == plugin_id:
                slot.filled_by = None
                slot.implementation = None
                count += 1
        return count

    def get(self, name: str) -> PluginSlot | None:
        return self._slots.get(name)
```

- [ ] **Step 3: 写 host.py**

```python
# core/plugins/host.py
"""PluginHost — 插件生命周期管理 + 热插拔。"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from dataclasses import dataclass, field

from .contract import PluginManifest, PluginAPI
from .sdk import PluginEntry
from .slots import SlotRegistry

from core.tools import ToolRegistry
from core.commands import CommandRegistry
from core.config import Config

logger = logging.getLogger(__name__)


@dataclass
class PluginInfo:
    manifest: PluginManifest
    loaded: bool = False
    api: PluginAPI | None = None
    module: object | None = None

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name or self.manifest.id


class PluginHost:
    """插件运行时 — 发现 → 校验 → 加载 → 激活 → 卸载。"""

    def __init__(
        self,
        config: Config,
        tool_registry: ToolRegistry,
        command_registry: CommandRegistry,
        slot_registry: SlotRegistry | None = None,
    ) -> None:
        self._config = config
        self._tool_registry = tool_registry
        self._command_registry = command_registry
        self._slot_registry = slot_registry or SlotRegistry()
        self._plugins: dict[str, PluginInfo] = {}

    # ── 发现 ──

    def discover(self) -> list[PluginManifest]:
        """扫描 plugins_dir 下所有子目录，返回发现的 manifest 列表。"""
        plugins_dir = self._config.plugins_dir
        if not plugins_dir.exists():
            return []

        manifests: list[PluginManifest] = []
        for entry in sorted(plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest = PluginManifest.from_dir(entry)
            if manifest is not None:
                manifests.append(manifest)
        return manifests

    # ── 加载/卸载 ──

    async def load(self, plugin_id: str) -> PluginInfo | None:
        """加载并激活单个插件。"""

        # 安全门：不允许路径逃逸
        plugins_dir = self._config.plugins_dir.resolve()
        plugin_dir = (plugins_dir / plugin_id).resolve()
        if not str(plugin_dir).startswith(str(plugins_dir)):
            logger.warning(f"拒绝加载插件 {plugin_id}: 路径逃逸")
            return None

        manifest = PluginManifest.from_dir(plugin_dir)
        if manifest is None:
            logger.warning(f"插件 {plugin_id} 无有效 manifest")
            return None

        entry_file = plugin_dir / manifest.entry
        if not entry_file.exists():
            logger.warning(f"插件 {plugin_id} 入口文件 {manifest.entry} 不存在")
            return None

        # 安全门：world-writable
        try:
            if entry_file.stat().st_mode & 0o002:
                logger.warning(f"拒绝加载插件 {plugin_id}: 文件可被他人写入")
                return None
        except OSError:
            pass

        # 导入模块
        try:
            module_name = f"aide_plugin_{manifest.id}"
            spec = importlib.util.spec_from_file_location(module_name, entry_file)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            logger.exception(f"加载插件 {plugin_id} 失败")
            return None

        # 找 register 入口
        register_fn: PluginEntry | None = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, "__aide_plugin_id__"):
                register_fn = attr
                break

        if register_fn is None:
            # fallback: 找名为 register 的函数
            register_fn = getattr(module, "register", None)

        if register_fn is None or not callable(register_fn):
            logger.warning(f"插件 {plugin_id} 无 register(api) 入口")
            sys.modules.pop(module_name, None)
            return None

        # 激活
        api = PluginAPI(plugin_id)
        try:
            register_fn(api)
        except Exception as e:
            logger.exception(f"插件 {plugin_id} register() 执行失败")
            sys.modules.pop(module_name, None)
            return None

        # 注册到 registry
        for tool in api._tools:
            self._tool_registry.register(tool)
        for cmd in api._commands:
            self._command_registry.register(cmd)
        for slot_name in api._provided_slots:
            self._slot_registry.declare(slot_name)

        # 调用启动钩子
        for hook in api._startup_hooks:
            try:
                hook()
            except Exception as e:
                logger.warning(f"插件 {plugin_id} 启动钩子失败: {e}")

        info = PluginInfo(manifest=manifest, loaded=True, api=api, module=module)
        self._plugins[plugin_id] = info
        logger.info(f"插件已加载: {plugin_id}")
        return info

    async def unload(self, plugin_id: str) -> bool:
        """卸载插件：dispose → 从 registry 移除 → 卸载模块。"""
        info = self._plugins.pop(plugin_id, None)
        if info is None:
            return False

        # 调用 shutdown 钩子
        if info.api:
            for hook in info.api._shutdown_hooks:
                try:
                    hook()
                except Exception as e:
                    logger.warning(f"插件 {plugin_id} shutdown 钩子失败: {e}")

        # 从 registries 移除
        source = f"plugin:{plugin_id}"
        self._command_registry.unregister_source(source)
        self._slot_registry.unfill(plugin_id)

        # 卸载 Python 模块
        module_name = f"aide_plugin_{plugin_id}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        logger.info(f"插件已卸载: {plugin_id}")
        return True

    async def reload(self, plugin_id: str) -> PluginInfo | None:
        await self.unload(plugin_id)
        return await self.load(plugin_id)

    def list_loaded(self) -> list[PluginInfo]:
        return list(self._plugins.values())

    def is_loaded(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins

    @property
    def slot_registry(self) -> SlotRegistry:
        return self._slot_registry
```

- [ ] **Step 4: 写 host 测试**

```python
# tests/plugins/test_host.py
import json
import pytest
from pathlib import Path
from core.config import Config
from core.tools import ToolRegistry
from core.commands import CommandRegistry
from core.plugins.host import PluginHost
from core.plugins.slots import SlotRegistry


@pytest.fixture
def host(tmp_path):
    config = Config(aide_root=tmp_path / ".aide")
    config.plugins_dir.mkdir(parents=True)
    tool_reg = ToolRegistry()
    cmd_reg = CommandRegistry()
    return PluginHost(config, tool_reg, cmd_reg)


class TestPluginHost:
    def test_discover_empty_dir(self, host):
        assert host.discover() == []

    def test_discover_finds_manifest(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "test-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "aide.plugin.json").write_text(
            json.dumps({"id": "test-plugin"}))
        manifests = host.discover()
        assert len(manifests) == 1
        assert manifests[0].id == "test-plugin"

    def test_discover_openclaw_manifest(self, host, tmp_path):
        plugin_dir = host._config.plugins_dir / "oc-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "openclaw.plugin.json").write_text(
            json.dumps({"id": "oc-plugin"}))
        manifests = host.discover()
        assert len(manifests) == 1
        assert manifests[0].id == "oc-plugin"

    def test_unload_nonexistent(self, host):
        assert not host.unload("nonexistent")  # async: use asyncio

    def test_slot_registry_default(self, host):
        assert isinstance(host.slot_registry, SlotRegistry)
```

- [ ] **Step 5: 运行测试**

```bash
uv run pytest tests/plugins/ -v
```

Expected: all plugin tests PASS

- [ ] **Step 6: Commit**

```bash
git add core/plugins/sdk.py core/plugins/host.py core/plugins/slots.py tests/plugins/test_host.py
git commit -m "feat(p4): add plugin SDK, Host, and Slot system"
```

---

### Task 12: Step 3 — 工具自动发现 + Part 1

**Files:**
- Create: `core/tools/discovery.py`
- Create: `tests/tools/test_discovery.py`

**Interfaces:**
- Consumes: `core.tools.ToolRegistry`, `core.tools.builtin`, `core.plugins.PluginHost`
- Produces: `discover_and_register(registry, plugin_host)` 函数

- [ ] **Step 1: 写 discovery.py**

```python
# core/tools/discovery.py
"""工具自动发现 — 内置工具 + 插件工具统一注册。"""

from core.tools import ToolRegistry
from core.tools.builtin import BUILTIN_TOOLS
from core.plugins.host import PluginHost


def register_builtin_tools(registry: ToolRegistry) -> int:
    """注册所有内置工具。返回注册数量。"""
    count = 0
    for module in BUILTIN_TOOLS:
        from core.tools import ToolDefinition
        tool = ToolDefinition(
            name=module.__name__.split(".")[-1],
            description=getattr(module, "description", ""),
            parameters=module.schema,
            execute=module.execute,
        )
        registry.register(tool)
        count += 1
    return count
```

`builtin/__init__.py` 需补充工具模块的 description 和 schema 导出——但这些已通过现有 `ToolDefinition` 在 `app.py` 中硬编码注册。自动发现有两种方案：
  - A: 保持 `app.py` 中 `_build_tool_registry()` 不变，`discovery.py` 只负责从插件收集额外工具
  - B: 每个内置工具模块直接暴露 `tool: ToolDefinition`，discovery 统一扫描

选 A（保守），discovery 只做插件部分：

```python
# core/tools/discovery.py (最终版)
"""工具发现辅助 — 从插件收集工具注册。"""

from core.tools import ToolRegistry


def register_plugin_tools(registry: ToolRegistry, plugin_host) -> int:
    """(插件工具已在 PluginHost.load() 中注册，此处为预留 API)"""
    return 0
```

- [ ] **Step 2: 写简单测试**

```python
# tests/tools/test_discovery.py
from core.tools import ToolRegistry
from core.tools.discovery import register_builtin_tools, register_plugin_tools


class TestDiscovery:
    def test_register_builtin_tools_adds_five(self):
        registry = ToolRegistry()
        count = register_builtin_tools(registry)
        assert count == 5
        names = registry.list_names()
        assert "read_file" in names
        assert "write_file" in names
        assert "run_shell" in names
        assert "search_memory" in names
        assert "web_search" in names

    def test_register_plugin_tools_noop(self):
        registry = ToolRegistry()
        count = register_plugin_tools(registry, None)
        assert count == 0
```

更新 `register_builtin_tools` 以匹配现有 `_build_tool_registry()` 的逻辑：

```python
# core/tools/discovery.py — 最终版本
"""工具自动发现 — 内置工具 + 插件工具统一注册。"""

from core.tools import ToolRegistry, ToolDefinition
from core.tools.builtin import read_file, write_file, run_shell, search_memory, web_search


def register_builtin_tools(registry: ToolRegistry) -> int:
    """注册所有内置工具。"""
    tools = [
        ("read_file", "读取本地文件内容。", read_file.schema, read_file.execute),
        ("write_file", "写入/创建本地文件。", write_file.schema, write_file.execute),
        ("run_shell", "执行 Shell 命令并返回输出。超时 30 秒。", run_shell.schema, run_shell.execute),
        ("search_memory", "搜索 Aide 的记忆数据。", search_memory.schema, search_memory.execute),
        ("web_search", "通过 DuckDuckGo 联网搜索。", web_search.schema, web_search.execute),
    ]
    for name, desc, params, exe in tools:
        registry.register(ToolDefinition(name=name, description=desc, parameters=params, execute=exe))
    return len(tools)


def register_plugin_tools(registry: ToolRegistry, plugin_host) -> int:
    """插件工具已在 PluginHost.load() 中注册。此函数为预留 API。"""
    return 0
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/tools/test_discovery.py -v
```

Expected: 2 tests PASS

- [ ] **Step 4: Commit**

```bash
git add core/tools/discovery.py tests/tools/test_discovery.py
git commit -m "feat(p4): add tool discovery — builtin registration + plugin API placeholder"
```

---

### Task 13: Step 3 — SessionManager

**Files:**
- Create: `core/sessions/manager.py`
- Create: `tests/sessions/test_manager.py`

**Interfaces:**
- Consumes: `core.config.Config`
- Produces: `SessionManager`, `SessionInfo`

- [ ] **Step 1: 写 SessionManager**

```python
# core/sessions/manager.py
"""SessionManager — 会话 CRUD + 智能标题。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SessionInfo:
    id: str
    name: str
    created_at: str = ""
    turn_count: int = 0


class SessionManager:
    """会话生命周期管理。"""

    def __init__(self, sessions_root: Path) -> None:
        self._root = sessions_root

    def create(self, first_msg: str) -> SessionInfo:
        """创建新会话：生成目录 + meta.json。"""
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        session_dir = self._root / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "messages").mkdir()

        name = self.derive_title(first_msg)
        meta = {"name": name, "created_at": datetime.now(timezone.utc).isoformat()}
        (session_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        return SessionInfo(id=session_id, name=name, created_at=meta["created_at"])

    def list_all(self) -> list[SessionInfo]:
        """列出所有会话（按创建时间倒序）。"""
        if not self._root.exists():
            return []

        sessions: list[SessionInfo] = []
        for entry in sorted(self._root.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            info = self._load_info(entry)
            if info:
                sessions.append(info)
        return sessions

    def get(self, session_id: str) -> SessionInfo | None:
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return None
        return self._load_info(session_dir)

    def delete(self, session_id: str) -> bool:
        import shutil
        session_dir = self._root / session_id
        if not session_dir.is_dir():
            return False
        shutil.rmtree(session_dir)
        return True

    def _load_info(self, session_dir: Path) -> SessionInfo | None:
        meta_path = session_dir / "meta.json"
        name = session_dir.name
        created_at = ""
        turn_count = 0

        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                name = meta.get("name", name)
                created_at = meta.get("created_at", "")
            except (json.JSONDecodeError, OSError):
                pass

        # 统计轮数
        messages_dir = session_dir / "messages"
        if messages_dir.exists():
            turn_count = len(list(messages_dir.glob("turn_*.json")))

        return SessionInfo(
            id=session_dir.name,
            name=name,
            created_at=created_at,
            turn_count=turn_count,
        )

    @staticmethod
    def derive_title(text: str, max_len: int = 20) -> str:
        """智能标题 — 规则引擎，零 LLM。"""
        text = text.strip()
        m = re.match(r'^(.*?)[。！？\n]', text)
        if m:
            text = m.group(1).strip()
        if not text:
            return "新对话"

        prefixes = [
            '能不能帮我', '可不可以帮我', '请帮我', '帮我',
            '能不能', '可不可以', '你可以', '你能',
            '怎么', '如何', '什么是', '什么',
            '我想', '我要', '我需要', '请', '来',
        ]
        for p in sorted(prefixes, key=len, reverse=True):
            if text.startswith(p) and len(text) > len(p) + 2:
                text = text[len(p):].strip()
                break

        if len(text) > max_len:
            text = text[:max_len] + "…"

        return text or "新对话"
```

- [ ] **Step 2: 写测试**

```python
# tests/sessions/test_manager.py
from pathlib import Path
from core.sessions.manager import SessionManager, SessionInfo


class TestSessionManager:
    def test_create_session(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        info = mgr.create("帮我写个Python脚本")
        assert info.name != "新对话"
        assert (tmp_path / "sessions" / info.id).is_dir()
        assert (tmp_path / "sessions" / info.id / "meta.json").exists()

    def test_list_all(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        mgr.create("first")
        mgr.create("second")
        sessions = mgr.list_all()
        assert len(sessions) == 2

    def test_get(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        info = mgr.create("test")
        found = mgr.get(info.id)
        assert found is not None
        assert found.name == info.name

    def test_delete(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        info = mgr.create("to delete")
        assert mgr.delete(info.id) is True
        assert not (tmp_path / "sessions" / info.id).exists()

    def test_derive_title_truncates(self):
        title = SessionManager.derive_title("帮我写一个Python脚本来处理CSV文件中的数据")
        assert len(title) <= 21  # 20 + "…"

    def test_derive_title_removes_prefix(self):
        title = SessionManager.derive_title("请帮我写个爬虫")
        assert not title.startswith("请帮我")
        assert len(title) > 0
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/sessions/test_manager.py -v
```

Expected: ~6 tests PASS

- [ ] **Step 4: Commit**

```bash
git add core/sessions/manager.py tests/sessions/test_manager.py
git commit -m "feat(p4): add SessionManager — CRUD + smart title derivation"
```

---

### Task 14: Step 3 — AgentKernel 门面 + Protocols

**Files:**
- Create: `core/kernel/protocols.py`
- Create: `core/kernel/agent.py`
- Create: `tests/kernel/test_agent.py`

**Interfaces:**
- Consumes: `FCLoop` (Task 5), `ContextPipeline` (Task 6), `SessionManager` (Task 13), `CaptureEngine` (Task 7), `PluginHost` (Task 11), `Config` (Task 1)
- Produces: `AgentKernel`, `ChatResult`, `TokenUsage`

- [ ] **Step 1: 写 protocols.py**

```python
# core/kernel/protocols.py
"""Kernel 协议定义 — ExecutorUI 等回调接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ExecutorUI(Protocol):
    """FC 循环 → UI 层回调接口。UI 层实现此接口，Kernel 零 Textual 依赖。"""

    def on_text_token(self, token: str) -> None: ...
    def on_text_done(self) -> None: ...
    def on_tool_start(self, tool_name: str, arguments: dict) -> None: ...
    def on_tool_done(self, tool_name: str, result: str) -> None: ...
    def on_tool_error(self, tool_name: str, error: str) -> None: ...
    def on_max_turns(self) -> None: ...
    def on_blocked(self) -> None: ...


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResult:
    conversation: list[dict]          # 更新后的完整对话
    assistant_text: str               # AI 回复文本
    captured_entries: list[dict]      # 本轮截获的条目
    usage: TokenUsage = TokenUsage()
```

- [ ] **Step 2: 写 AgentKernel 门面**

```python
# core/kernel/agent.py
"""AgentKernel — Aide 内核门面。

编排 6 个子组件，不实现逻辑，每个方法 ≤ 10 行。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .protocols import ExecutorUI, ChatResult, TokenUsage
from .fc_loop import FunctionCallingLoop

from core.config import Config
from core.llm_gateway import AbstractProvider
from core.tools import ToolRegistry
from core.commands import CommandRegistry
from core.context import ContextPipeline, ContextIngester, ContextCompactor
from core.memory import CaptureEngine, EntryManager, PromptUpdater, TopicFrequencyTracker
from core.plugins.host import PluginHost
from core.plugins.slots import SlotRegistry
from core.sessions.manager import SessionManager
from core.storage import JsonStore

logger = logging.getLogger(__name__)


class AgentKernel:
    """Aide 内核 — 零 UI 依赖，可独立测试。"""

    def __init__(
        self,
        config: Config,
        provider: AbstractProvider,
        tool_registry: ToolRegistry,
        command_registry: CommandRegistry,
        context_pipeline: ContextPipeline,
        ingester: ContextIngester,
        compactor: ContextCompactor,
        session_manager: SessionManager,
        capture_engine: CaptureEngine,
        entry_manager: EntryManager,
        prompt_updater: PromptUpdater,
        topic_tracker: TopicFrequencyTracker,
        plugin_host: PluginHost,
        slot_registry: SlotRegistry,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tool_registry = tool_registry
        self.command_registry = command_registry
        self._pipeline = context_pipeline
        self._ingester = ingester
        self._compactor = compactor
        self._sessions = session_manager
        self._capture = capture_engine
        self._entries = entry_manager
        self._updater = prompt_updater
        self._tracker = topic_tracker
        self._plugins = plugin_host
        self._slots = slot_registry
        self._fc_loop = FunctionCallingLoop(provider, tool_registry)

    # ── 核心 ──

    async def chat(
        self,
        user_msg: str,
        session_dir: Path,
        turn: int,
        conversation: list[dict],
        ui: ExecutorUI,
    ) -> ChatResult:
        """执行一轮对话。"""
        assistant_text = ""

        # 1. 组装上下文
        system_msgs, trimmed_conv = await self._pipeline.assemble(
            session_dir, user_msg, conversation,
        )
        full_messages = system_msgs + trimmed_conv

        # 2. FC 循环
        updated = await self._fc_loop.run(full_messages, ui=ui)

        # 合并对话历史
        from core.context.relevance import _split_conversation
        older, _ = _split_conversation(conversation)
        new_conversation = older + updated

        # 提取 AI 回复
        for msg in reversed(updated):
            if msg.get("role") == "assistant" and msg.get("content"):
                assistant_text = msg["content"]
                break

        # 3. 摄入存储
        await self._ingester.ingest(
            turn=turn,
            user_msg=user_msg,
            assistant_msg=assistant_text,
            conversation=new_conversation,
        )

        # 4. 截获条目
        captured = await self._capture.capture(
            user_msg=user_msg,
            assistant_msg=assistant_text,
            session_id=session_dir.name,
            turn=turn,
        )

        return ChatResult(
            conversation=new_conversation,
            assistant_text=assistant_text,
            captured_entries=captured,
        )

    # ── 会话 ──

    async def create_session(self, first_msg: str) -> tuple[SessionManager, Path]:
        info = self._sessions.create(first_msg)
        session_dir = self._sessions._root / info.id
        return info, session_dir

    async def list_sessions(self):
        return self._sessions.list_all()

    async def delete_session(self, session_id: str) -> bool:
        return self._sessions.delete(session_id)

    # ── 插件 ──

    async def load_plugin(self, plugin_id: str):
        return await self._plugins.load(plugin_id)

    async def unload_plugin(self, plugin_id: str) -> bool:
        return await self._plugins.unload(plugin_id)

    def list_plugins(self):
        return self._plugins.list_loaded()

    # ── 压缩 ──

    async def compact_session(self, session_dir: Path):
        return await self._compactor.compact(session_dir)

    # ── prompt 更新 ──

    async def update_profile(self):
        return await self._updater.update_all()

    def flush_cache(self) -> None:
        self._pipeline.flush_cache()
```

- [ ] **Step 3: 写测试**

```python
# tests/kernel/test_agent.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from core.kernel.agent import AgentKernel, ChatResult
from core.config import Config


@pytest.fixture
def kernel(tmp_path):
    config = Config(aide_root=tmp_path / ".aide")
    provider = MagicMock()
    tool_reg = MagicMock()
    cmd_reg = MagicMock()
    pipeline = AsyncMock()
    pipeline.assemble.return_value = ([], [])
    ingester = AsyncMock()
    compactor = AsyncMock()
    session_mgr = MagicMock()
    capture = AsyncMock()
    capture.capture.return_value = []
    entry_mgr = MagicMock()
    updater = AsyncMock()
    tracker = MagicMock()
    plugin_host = MagicMock()
    slots_reg = MagicMock()

    return AgentKernel(
        config, provider, tool_reg, cmd_reg,
        pipeline, ingester, compactor,
        session_mgr, capture, entry_mgr, updater, tracker,
        plugin_host, slots_reg,
    )


class TestAgentKernel:
    def test_list_sessions_delegates(self, kernel):
        kernel._sessions.list_all.return_value = []
        assert kernel.list_sessions() == []

    def test_delete_session(self, kernel):
        kernel._sessions.delete.return_value = True
        assert kernel.delete_session("test-id") is True

    def test_flush_cache_delegates(self, kernel):
        kernel.flush_cache()  # 不应抛异常
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/kernel/test_agent.py -v
```

Expected: ~3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/kernel/protocols.py core/kernel/agent.py tests/kernel/test_agent.py
git commit -m "feat(p4): add AgentKernel facade + protocols (ChatResult, TokenUsage, ExecutorUI)"
```

---

### Task 15: Step 3 — UIBridge

**Files:**
- Create: `ui/textual_app/bridge.py`
- Modify: `ui/textual_app/app.py` (后续 Task 16 接线)

**Interfaces:**
- Consumes: `AgentKernel` (Task 14), Textual widgets
- Produces: `UIBridge` — 实现 ExecutorUI，桥接 kernel ↔ Textual

- [ ] **Step 1: 写 UIBridge**

```python
# ui/textual_app/bridge.py
"""UIBridge — kernel ↔ Textual 桥接层。

实现 ExecutorUI Protocol，把 kernel 事件翻译为 Textual widget 操作。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.kernel.protocols import ExecutorUI

if TYPE_CHECKING:
    from .app import AideApp
    from .widgets.message_list import MessageList
    from .widgets.input_box import InputBox
    from .widgets.status_bar import StatusBar

logger = logging.getLogger(__name__)


class UIBridge:
    """kernel ↔ Textual 桥接器。

    用法:
        bridge = UIBridge(app)
        result = await kernel.chat(msg, session_dir, turn, conv, ui=bridge)
    """

    def __init__(self, app: "AideApp") -> None:
        self._app = app
        self._last_ai_text: str = ""

    # ── ExecutorUI 实现 ──

    def on_text_token(self, token: str) -> None:
        self._last_ai_text += token
        self._app.query_one("#messages", "MessageList").add_ai_chunk(token)

    def on_text_done(self) -> None:
        msg_list: "MessageList" = self._app.query_one("#messages", "MessageList")
        if msg_list.has_pending():
            self._last_ai_text = msg_list.finish_ai_message()

    def on_tool_start(self, tool_name: str, arguments: dict) -> None:
        pass  # 不显示工具调用

    def on_tool_done(self, tool_name: str, result: str) -> None:
        pass

    def on_tool_error(self, tool_name: str, error: str) -> None:
        pass

    def on_max_turns(self) -> None:
        self._app.query_one("#messages", "MessageList").add_system_notice(
            "已达到最大工具调用轮次 (5)。任务可能需要你手动介入。"
        )

    def on_blocked(self) -> None:
        self._app.query_one("#messages", "MessageList").add_system_notice(
            "工具执行遇到错误，已暂停。请查看上方错误信息并决定下一步。"
        )

    # ── 文本收集 ──

    @property
    def last_ai_text(self) -> str:
        return self._last_ai_text

    def reset_text(self) -> None:
        self._last_ai_text = ""
```

- [ ] **Step 2: Commit**

```bash
git add ui/textual_app/bridge.py
git commit -m "feat(p4): add UIBridge — kernel-to-Textual adapter implementing ExecutorUI"
```

---

### Task 16: Step 4 — 接线：app.py 切换到 AgentKernel

**Files:**
- Modify: `ui/textual_app/app.py` (重大改写)

**Interfaces:**
- Consumes: `AgentKernel` (Task 14), `UIBridge` (Task 15), `Config` (Task 1)
- Produces: 重构后的 `AideApp` — 只做 Textual 逻辑 + 委托 kernel

- [ ] **Step 1: 在 app.py 中创建 Kernel 和 Bridge**

修改 `on_mount` — 用 `Config.load()` 和 `AgentKernel` 替代直接创建各组件：

```python
# ui/textual_app/app.py — on_mount 前半部分替换

async def on_mount(self) -> None:
    msg_list = self.query_one("#messages", MessageList)

    # ── 加载配置 ──
    try:
        self._config = Config.load()
        self.provider = create_provider(self._config.llm)
        self._model_name = self._config.llm.model or self._config.llm.provider
    except FileNotFoundError as e:
        msg_list.add_error(str(e))
        self.provider = None
        self._model_name = "未配置"
    except Exception as e:
        msg_list.add_error(f"配置加载失败: {e}")
        self.provider = None
        self._model_name = "错误"

    # ── 工具注册 ──
    self._tool_registry = _build_tool_registry()
    self._cmd_registry = CommandRegistry()
    from core.commands.builtin.handlers import register_builtin_commands
    register_builtin_commands(self._cmd_registry)

    # ── 存储 ──
    self._store = JsonStore(self._config.aide_root)
    await self._store.start()

    # ── 子组件 ──
    self._ingester = ContextIngester(self._store)
    self._pipeline = ContextPipeline()
    self._compactor = ContextCompactor(self.provider, self._store)
    self._entry_mgr = EntryManager(self._store)
    self._tracker = TopicFrequencyTracker(self._store)
    self._capture = CaptureEngine(self._entry_mgr, self._tracker)
    self._updater = PromptUpdater(
        self.provider, self._entry_mgr,
        on_cache_flush=self._pipeline.flush_cache,
    )
    self._session_mgr = SessionManager(self._config.sessions_root)
    self._slot_registry = SlotRegistry()
    self._plugin_host = PluginHost(
        self._config, self._tool_registry, self._cmd_registry, self._slot_registry,
    )

    # ── Kernel ──
    self._kernel = AgentKernel(
        config=self._config,
        provider=self.provider,
        tool_registry=self._tool_registry,
        command_registry=self._cmd_registry,
        context_pipeline=self._pipeline,
        ingester=self._ingester,
        compactor=self._compactor,
        session_manager=self._session_mgr,
        capture_engine=self._capture,
        entry_manager=self._entry_mgr,
        prompt_updater=self._updater,
        topic_tracker=self._tracker,
        plugin_host=self._plugin_host,
        slot_registry=self._slot_registry,
    )

    # ── Bridge ──
    self._bridge = UIBridge(self)

    # ── 修复 ExecutorUI 引用 ──
    # 旧的 self._executor 替换为 kernel 的 fc_loop
    # (旧代码路径保留注释)

    # ── 对话状态 ──
    self._session_ensured = False
    self._turn = 0
    self.conversation: list[dict] = []
    self._last_user_text: str = ""
    self._current_session_name = ""
    self._maintenance = False

    # ── 状态栏 ──
    status_bar = self.query_one("#status-bar", StatusBar)
    status_bar.update_info(model=self._model_name)

    # ── 系统托盘 ──
    self._tray = TrayManager(self)
    self._tray.start()

    # ── 冷启动 → 引导 → 首页 ──
    self._startup_worker()
```

- [ ] **Step 2: 重写 chat_worker — 委托给 kernel**

```python
@work(exclusive=True, thread=False)
async def chat_worker(self) -> None:
    """异步 worker：委托给 kernel.chat()。"""
    self._bridge.reset_text()

    try:
        # 延迟创建 session
        if not self._session_ensured:
            info, session_dir = await self._kernel.create_session(self._last_user_text)
            self._ingester.ensure_session(info.id)
            self._session_ensured = True
            self._turn = 1
            self._current_session_name = info.name
            # 更新 UI 标签
            self.query_one("#session-label", Static).update(f" {info.name}")
        else:
            self._turn += 1
            session_dir = self._ingester._session_dir

        result = await self._kernel.chat(
            user_msg=self._last_user_text,
            session_dir=session_dir,
            turn=self._turn,
            conversation=self.conversation,
            ui=self._bridge,
        )

        self.conversation = result.conversation

    except Exception as e:
        msg_list = self.query_one("#messages", MessageList)
        if msg_list.has_pending():
            msg_list.finish_ai_message()
        msg_list.add_error(f"执行异常: {e}")
    finally:
        self._update_status_bar()
        input_box = self.query_one("#input", InputBox)
        input_box.disabled = False
        input_box.focus()
```

- [ ] **Step 3: 重写 _handle_new_session — 使用 SessionManager**

```python
@on(NewSessionRequested)
def _handle_new_session(self, event: NewSessionRequested) -> None:
    msg = event.first_message
    info, _ = self._kernel.create_session(msg)  # SessionManager 已写入 meta.json
    self._enter_session(session_id=info.id, name=info.name, first_message=msg)
```

- [ ] **Step 4: 重写 _restore_session — 使用 SessionManager**

```python
def _restore_session(self, session_id: str) -> None:
    session_dir = self._config.sessions_root / session_id
    if not session_dir.exists():
        return

    messages_dir = session_dir / "messages"
    if not messages_dir.exists():
        return

    turn_files = sorted(messages_dir.glob("turn_*.json"))
    self._turn = len(turn_files)
    self.conversation = []

    for tf in turn_files[-30:]:
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            self.conversation.append({"role": "user", "content": data.get("user", "")})
            assistant = data.get("assistant", "")
            if assistant:
                self.conversation.append({"role": "assistant", "content": assistant})
        except (json.JSONDecodeError, OSError):
            pass
```

- [ ] **Step 5: 更新命令路由 — 使用 CommandRegistry**

```python
async def on_input_box_user_submitted(self, event: InputBox.UserSubmitted) -> None:
    # ... (前面不变)

    # ── / 命令路由 ──
    command = self._cmd_registry.route(text)
    if command is not None:
        handler, args = command
        await self._run_command(handler, args, msg_list, input_box, text)
        return

    # ... (后面不变)
```

- [ ] **Step 6: 删除 app.py 中不再需要的方法**

删除：
- `_build_tool_registry()` — 改为用 `discovery.register_builtin_tools()`
- `_derive_title()` — 改为用 `SessionManager.derive_title()`
- `on_text_token`, `on_text_done`, `on_tool_start`, `on_tool_done`, `on_tool_error`, `on_max_turns`, `on_blocked` — 改为 UIBridge 实现

- [ ] **Step 7: 运行全量测试**

```bash
uv run pytest tests/ -q
```

- [ ] **Step 8: 手动验证**

```bash
uv run python shell/main.py
```

测试：创建新会话 → 发送消息 → Esc 回首页 → 切换会话 → /profile → /help → /compress

- [ ] **Step 9: Commit**

```bash
git add ui/textual_app/app.py
git commit -m "refactor(p4): wire app.py to AgentKernel — delegate chat/session/commands to kernel"
```

---

### Task 17: Step 4 — /plugin 指令

**Files:**
- Create: `core/commands/builtin/plugin_commands.py`
- Modify: `core/commands/builtin/handlers.py` (注册 /plugin 指令)

**Interfaces:**
- Consumes: `PluginHost` (Task 11)
- Produces: `/plugin load <id>`, `/plugin unload <id>`, `/plugin reload <id>`, `/plugin list`

- [ ] **Step 1: 写 plugin_commands.py**

```python
# core/commands/builtin/plugin_commands.py
"""/plugin 指令 — 插件管理。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)


async def handle_plugin(app, args: str) -> str:
    """插件管理入口。

    子命令:
      load <id>    — 加载插件
      unload <id>  — 卸载插件
      reload <id>  — 重载插件
      list         — 列出已加载插件
      discover     — 扫描发现可用插件
    """
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    kernel = app._kernel

    if sub == "list":
        plugins = kernel.list_plugins()
        if not plugins:
            # 尝试发现
            manifests = kernel._plugins.discover()
            if not manifests:
                return "📦 没有已加载的插件，也没有发现可用插件。\n\n将插件放入 `~/.aide/plugins/` 目录。"
            lines = ["## 可用插件（未加载）\n"]
            for m in manifests:
                loaded = kernel._plugins.is_loaded(m.id)
                status = "✅ 已加载" if loaded else "⏳ 未加载"
                lines.append(f"- **{m.name or m.id}** ({m.version}) — {status}")
                if m.description:
                    lines.append(f"  {m.description}")
            lines.append("\n使用 `/plugin load <id>` 加载插件。")
            return "\n".join(lines)

        lines = ["## 已加载插件\n"]
        for p in plugins:
            lines.append(f"- **{p.name}** v{p.manifest.version}")
            if p.manifest.description:
                lines.append(f"  {p.manifest.description}")
        return "\n".join(lines)

    elif sub == "load":
        if not rest:
            return "⚠️ 用法：`/plugin load <插件ID>`\n先用 `/plugin discover` 查看可用插件。"
        info = await kernel.load_plugin(rest)
        if info:
            return f"✅ 插件已加载：**{info.name}** v{info.manifest.version}"
        return f"❌ 加载插件失败：`{rest}`\n请检查 manifest 和 entry 文件是否存在。"

    elif sub == "unload":
        if not rest:
            return "⚠️ 用法：`/plugin unload <插件ID>`"
        if await kernel.unload_plugin(rest):
            return f"✅ 插件已卸载：`{rest}`"
        return f"❌ 插件 `{rest}` 未加载或不存在。"

    elif sub == "reload":
        if not rest:
            return "⚠️ 用法：`/plugin reload <插件ID>`"
        info = await kernel._plugins.reload(rest)
        if info:
            return f"✅ 插件已重载：**{info.name}** v{info.manifest.version}"
        return f"❌ 重载插件失败：`{rest}`"

    elif sub == "discover":
        manifests = kernel._plugins.discover()
        if not manifests:
            return "📦 未发现可用插件。\n\n将插件放入 `~/.aide/plugins/<plugin-id>/` 目录，包含 `aide.plugin.json` 或 `openclaw.plugin.json`。"
        lines = ["## 发现的插件\n"]
        for m in manifests:
            loaded = kernel._plugins.is_loaded(m.id)
            status = "✅ 已加载" if loaded else "⏳ 未加载"
            lines.append(f"- **{m.id}** ({m.version}) — {status}")
            if m.description:
                lines.append(f"  {m.description}")
        return "\n".join(lines)

    else:
        return f"⚠️ 未知子命令：`{sub}`\n可用：`load`, `unload`, `reload`, `list`, `discover`"
```

- [ ] **Step 2: 注册 /plugin 指令**

```python
# core/commands/builtin/handlers.py — 在 register_builtin_commands() 中追加
from core.commands.builtin.plugin_commands import handle_plugin

def register_builtin_commands(registry: CommandRegistry) -> None:
    # ... 原有注册 ...
    registry.register(CommandDefinition(
        name="/plugin", description="管理插件：load/unload/reload/list/discover",
        handler=handle_plugin,
    ))
```

- [ ] **Step 3: 运行全量测试**

```bash
uv run pytest tests/ -q
```

- [ ] **Step 4: Commit**

```bash
git add core/commands/builtin/plugin_commands.py core/commands/builtin/handlers.py
git commit -m "feat(p4): add /plugin command — load/unload/reload/list/discover"
```

---

### Task 18: Step 4 — 创建示例插件

**Files:**
- Create: `~/.aide/plugins/hello-plugin/aide.plugin.json`
- Create: `~/.aide/plugins/hello-plugin/__init__.py`

**Interfaces:**
- Consumes: 插件系统 (Task 10-11)
- Produces: 一个可加载的示例插件，演示完整工作流

- [ ] **Step 1: 写 manifest**

```json
{
  "id": "hello-plugin",
  "name": "Hello Plugin",
  "version": "1.0.0",
  "description": "Aide 示例插件 — 演示工具注册",
  "kind": "tool",
  "entry": "__init__.py"
}
```

- [ ] **Step 2: 写插件代码**

```python
# ~/.aide/plugins/hello-plugin/__init__.py
"""Hello Plugin — Aide 示例插件。"""

from core.plugins.sdk import define_plugin
from core.plugins.contract import PluginAPI
from core.tools import ToolDefinition


HELLO_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "要打招呼的名字",
        },
    },
    "required": ["name"],
}


async def hello_execute(arguments: dict) -> str:
    name = arguments.get("name", "World")
    return f"Hello, {name}! 👋 来自 Aide 插件的问候。"


@define_plugin("hello-plugin")
def register(api: PluginAPI) -> None:
    api.register_tool(ToolDefinition(
        name="hello",
        description="向指定名字打招呼（示例插件工具）",
        parameters=HELLO_SCHEMA,
        execute=hello_execute,
    ))
```

- [ ] **Step 3: 验证插件可加载**

启动 Aide，输入 `/plugin discover` → 看到 hello-plugin → `/plugin load hello-plugin` → 看到成功消息。

- [ ] **Step 4: Commit**

```bash
# 示例插件在 ~/.aide/ 下，不在 git 管理范围
# 把模板放在项目中，install 时复制
mkdir -p d:/SEAI/Aide/core/plugins/templates/hello-plugin
# 将上述两个文件写入 d:/SEAI/Aide/core/plugins/templates/hello-plugin/

git add core/plugins/templates/
git commit -m "feat(p4): add hello-plugin example template"
```

---

### Task 19: Step 4 — 记忆召回 recall.py

**Files:**
- Create: `core/memory/recall.py`
- Create: `tests/memory/test_recall.py`

**Interfaces:**
- Consumes: EntryManager (Task 7), ~/.aide/ 数据目录
- Produces: `recall(query: str)` → 升级版搜索，支持 synonym map + time decay

- [ ] **Step 1: 写 recall.py（P4 Batch 1 版本 — 基本召回）**

```python
# core/memory/recall.py
"""记忆召回 — 跨会话搜索 + 相关性排序。

P4 Batch 1: 增强关键词匹配，加 synonym map。
P4 Batch 2 将加入时间衰减和语义相似度。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 简单同义词映射（Batch 2 将扩展）
SYNONYM_MAP = {
    "代码": ["编程", "程序", "脚本", "coding", "编程语言"],
    "文件": ["文档", "档案", "file"],
    "设置": ["配置", "config", "偏好"],
    "风格": ["偏好", "习惯", "style"],
    "错误": ["bug", "异常", "error", "问题"],
}


def _expand_query(query: str) -> set[str]:
    """扩展查询词的同义词。"""
    terms = set(query.lower().split())
    for word in list(terms):
        for key, synonyms in SYNONYM_MAP.items():
            if word in synonyms or word == key.lower():
                terms.add(key.lower())
                terms.update(s.lower() for s in synonyms)
    return terms


async def recall(
    query: str,
    aide_root: Path | None = None,
    entry_manager=None,
) -> list[dict]:
    """搜索记忆数据，返回相关结果。

    Args:
        query: 搜索关键词
        aide_root: ~/.aide/ 根目录
        entry_manager: EntryManager 实例（用于搜索条目目录）

    Returns:
        匹配结果列表，每项: {"source": str, "snippet": str, "score": float}
    """
    if aide_root is None:
        aide_root = Path.home() / ".aide"

    keywords = _expand_query(query)
    matches: list[dict] = []

    # 1. 搜索会话数据
    sessions_root = aide_root / "sessions"
    if sessions_root.exists():
        for session_dir in sorted(sessions_root.iterdir(), reverse=True):
            if not session_dir.is_dir():
                continue
            _search_session(session_dir, keywords, matches)

    # 2. 搜索条目目录
    if entry_manager is not None:
        await _search_entries(entry_manager, keywords, matches)

    # 3. 排序
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:15]


def _search_session(session_dir: Path, keywords: set[str], matches: list[dict]) -> None:
    """搜索一个会话目录。"""
    # meta.json
    meta_path = session_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            name = meta.get("name", "")
            score = _keyword_score(name, keywords)
            if score > 0:
                matches.append({
                    "source": f"[会话 {session_dir.name}]",
                    "snippet": f"会话：{name}",
                    "score": score * 1.5,  # meta 加权
                })
        except (json.JSONDecodeError, OSError):
            pass

    # overview.json
    overview_path = session_dir / "overview.json"
    if overview_path.exists():
        try:
            data = json.loads(overview_path.read_text(encoding="utf-8"))
            for field in ["topics", "preferences", "corrections", "decisions"]:
                items = data.get(field, [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, str):
                            score = _keyword_score(item, keywords)
                            if score > 0:
                                matches.append({
                                    "source": f"[会话 {session_dir.name} / {field}]",
                                    "snippet": item[:200],
                                    "score": score + 1,
                                })
        except (json.JSONDecodeError, OSError):
            pass


async def _search_entries(entry_manager, keywords: set[str], matches: list[dict]) -> None:
    """搜索条目目录。"""
    for entry_type, label in [
        ("preferences", "偏好条目"),
        ("workflows", "工作流条目"),
        ("long_term_memory", "长记忆条目"),
    ]:
        try:
            entries = await entry_manager.load(entry_type)
            for entry in entries:
                content = entry.get("content", "")
                status = entry.get("status", "?")
                score = _keyword_score(content, keywords)
                if score > 0:
                    matches.append({
                        "source": f"[{label}] status={status}",
                        "snippet": content[:200],
                        "score": score,
                    })
        except Exception:
            continue


def _keyword_score(text: str, keywords: set[str]) -> float:
    """计算文本对关键词的匹配分数。"""
    text_lower = text.lower()
    score = 0.0
    for kw in keywords:
        if kw in text_lower:
            score += 1.0
    return score
```

- [ ] **Step 2: 写测试**

```python
# tests/memory/test_recall.py
import pytest
from pathlib import Path
from core.memory.recall import recall, _expand_query, _keyword_score


class TestExpandQuery:
    def test_synonym_expansion(self):
        terms = _expand_query("代码风格")
        assert "代码" in terms or "编程" in terms
        assert "风格" in terms or "style" in terms

    def test_no_match_returns_original(self):
        terms = _expand_query("xyz")
        assert "xyz" in terms


class TestKeywordScore:
    def test_exact_match(self):
        assert _keyword_score("我喜欢简洁的代码", {"代码"}) == 1.0

    def test_partial_match(self):
        assert _keyword_score("Python编程风格", {"python", "风格"}) == 2.0

    def test_no_match(self):
        assert _keyword_score("hello world", {"中文"}) == 0.0


class TestRecall:
    @pytest.mark.asyncio
    async def test_recall_empty_dir(self, tmp_path):
        results = await recall("test", aide_root=tmp_path)
        assert results == []

    @pytest.mark.asyncio
    async def test_recall_finds_session(self, tmp_path):
        # 创建模拟会话
        import json
        sessions_dir = tmp_path / "sessions" / "20260701_120000"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "meta.json").write_text(
            json.dumps({"name": "Python脚本"}))
        (sessions_dir / "overview.json").write_text(
            json.dumps({"topics": ["编写Python脚本处理CSV"], "decisions": []}))

        results = await recall("Python", aide_root=tmp_path)
        assert len(results) > 0
        assert any("Python" in r["snippet"] for r in results)
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/memory/test_recall.py -v
```

- [ ] **Step 4: Commit**

```bash
git add core/memory/recall.py tests/memory/test_recall.py
git commit -m "feat(p4): add memory recall with synonym expansion and keyword scoring"
```

---

### Task 20: Step 4 — MCP adapter 骨架

**Files:**
- Create: `core/tools/mcp/adapter.py`
- Create: `tests/tools/test_mcp.py`

**Interfaces:**
- Consumes: `core.tools.ToolDefinition`, `core.tools.protocol.ToolProtocol`
- Produces: `MCPAdapter` — 将 MCP 服务端工具映射为 Aide ToolDefinition

- [ ] **Step 1: 写 MCP adapter 骨架**

```python
# core/tools/mcp/adapter.py
"""MCP Adapter — 将 MCP (Model Context Protocol) 工具映射为 Aide ToolDefinition。

P4 Batch 1: 骨架 + 协议定义。Batch 2: 完整 stdio/HTTP transport。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from core.tools import ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP 服务端连接配置。"""
    name: str
    command: str = ""        # stdio transport: 可执行文件路径
    args: list[str] = None   # stdio transport: 命令行参数
    url: str = ""            # HTTP transport: 服务端 URL


class MCPTransport(Protocol):
    """MCP Transport 协议。"""

    async def connect(self, config: MCPServerConfig) -> None: ...
    async def list_tools(self) -> list[dict]: ...
    async def call_tool(self, name: str, arguments: dict) -> str: ...
    async def disconnect(self) -> None: ...


class MCPAdapter:
    """MCP → Aide 工具适配器。

    P4 Batch 1: 骨架实现，不做实际连接。
    P4 Batch 2: 实现 stdio + HTTP transport。
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}

    def add_server(self, config: MCPServerConfig) -> None:
        """注册一个 MCP 服务端（暂不连接）。"""
        self._servers[config.name] = config

    async def discover_tools(self, server_name: str) -> list[ToolDefinition]:
        """从 MCP 服务端发现工具。

        P4 Batch 1: 返回空列表（骨架）。
        P4 Batch 2: 实际连接并列出工具。
        """
        if server_name not in self._servers:
            return []
        logger.info(f"[MCP] 工具发现暂未实现（Batch 2）: {server_name}")
        return []

    def list_servers(self) -> list[MCPServerConfig]:
        return list(self._servers.values())
```

- [ ] **Step 2: 写测试**

```python
# tests/tools/test_mcp.py
from core.tools.mcp.adapter import MCPAdapter, MCPServerConfig


class TestMCPAdapter:
    def test_add_server(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="test", command="echo"))
        assert "test" in adapter._servers

    def test_list_servers(self):
        adapter = MCPAdapter()
        adapter.add_server(MCPServerConfig(name="a"))
        adapter.add_server(MCPServerConfig(name="b"))
        assert len(adapter.list_servers()) == 2

    async def test_discover_tools_empty(self):
        adapter = MCPAdapter()
        tools = await adapter.discover_tools("nonexistent")
        assert tools == []
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/tools/test_mcp.py -v
```

- [ ] **Step 4: Commit**

```bash
git add core/tools/mcp/adapter.py tests/tools/test_mcp.py
git commit -m "feat(p4): add MCP adapter skeleton — MCPServerConfig, MCPAdapter, MCPTransport protocol"
```

---

### Task 21: Step 4 — headless 入口（预留）

**Files:**
- Create: `ui/headless.py`

**Interfaces:**
- Consumes: `AgentKernel` (Task 14)
- Produces: 纯 CLI 入口预留，供社区扩展

- [ ] **Step 1: 写 headless.py 骨架**

```python
# ui/headless.py
"""Headless CLI 入口 — 供社区开发非终端 UI 使用。

P4 Batch 1: 预留 API，不实际实现。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.config import Config
from core.kernel.agent import AgentKernel


class HeadlessUI:
    """纯 CLI 调用接口示例。

    社区可基于 AgentKernel 构建 Web UI、Desktop GUI 等。
    """

    def __init__(self, config_path: str | None = None):
        self.config = Config.load()
        # 初始化 kernel（需要 provider 等完整对接，此处为骨架）
        self._kernel = None  # AgentKernel(...) in full implementation

    async def send_message(self, user_msg: str, session_id: str | None = None) -> str:
        """(预留) 发送消息，返回 AI 回复文本。"""
        raise NotImplementedError("P4 Batch 2")
```

- [ ] **Step 2: Commit**

```bash
git add ui/headless.py
git commit -m "feat(p4): add headless CLI entry skeleton for community API"
```

---

### Task 22: Step 5 — 清理 & 收尾

**Files:**
- Delete: `core/executor/loop.py`, `core/executor/state.py`（shim 已生效）
- Delete: `core/context_manager/assembler.py`, `core/context_manager/ingester.py`, `core/context_manager/compactor.py`
- Delete: `core/prompt_manager/capture.py`, `core/prompt_manager/entries.py`, `core/prompt_manager/updater.py`, `core/prompt_manager/topic_tracker.py`
- Delete: `config/settings.py`（shim 已不再被 import）
- Delete: `config/` 目录下的旧配置文件
- Modify: `core/executor/__init__.py`（删除 shim，直接 re-export）
- Modify: `core/context_manager/__init__.py`（删除 shim，直接 re-export）
- Modify: `core/prompt_manager/__init__.py`（删除 shim，直接 re-export）
- Modify: `ui/textual_app/commands/__init__.py`（删除 shim，直接 re-export）
- Modify: `CLAUDE.md`（更新目录结构、命令、架构说明）

- [ ] **Step 1: 确认所有 import 已切换到新路径**

```bash
# 检查是否还有 from core.executor import 的引用（排除 __init__.py shim）
cd d:/SEAI/Aide && grep -r "from core.executor" --include="*.py" | grep -v "__init__.py"
# 应该为空或只有 __init__.py 自身
```

- [ ] **Step 2: 删除旧源文件，保留 shim __init__.py**

```bash
rm d:/SEAI/Aide/core/executor/loop.py
rm d:/SEAI/Aide/core/executor/state.py
rm d:/SEAI/Aide/core/context_manager/assembler.py
rm d:/SEAI/Aide/core/context_manager/ingester.py
rm d:/SEAI/Aide/core/context_manager/compactor.py
rm d:/SEAI/Aide/core/prompt_manager/capture.py
rm d:/SEAI/Aide/core/prompt_manager/entries.py
rm d:/SEAI/Aide/core/prompt_manager/updater.py
rm d:/SEAI/Aide/core/prompt_manager/topic_tracker.py
```

- [ ] **Step 3: 更新 shim __init__.py — 改为纯 re-export**

`core/executor/__init__.py`, `core/context_manager/__init__.py`, `core/prompt_manager/__init__.py` 保持 shim 但去掉"向后兼容"注释——它们现在是正式 API。

- [ ] **Step 4: 运行全量测试**

```bash
uv run pytest tests/ -q -v
```

Expected: ALL tests PASS (63 existing + new)

- [ ] **Step 5: 更新 CLAUDE.md**

更新目录结构、架构说明、新增命令（/plugin）、新的配置位置（~/.aide/config/）、Kernel/UI 分离说明。

- [ ] **Step 6: 最终手动验证**

```bash
uv run python shell/main.py
```

完整功能测试：首页 → 新会话 → 消息 → Esc → 切换会话 → /profile → /help → /compress → /plugin list → 托盘 → 退出

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore(p4): cleanup — remove old source files, update shims, refresh CLAUDE.md"
```

---

## 任务汇总

| Task | 描述 | 新建文件 | 修改文件 | 新增测试 |
|------|------|---------|---------|---------|
| 1 | Config dataclass | 2 | 0 | 7 |
| 2 | Config shim + gateway | 0 | 3 | 0 |
| 3 | JsonStore base_dir | 0 | 1 | 0 |
| 4 | 新目录结构 | 15 | 0 | 0 |
| 5 | executor → kernel | 2 | 1 | 0 |
| 6 | context_manager → context | 5 | 2 | 0 |
| 7 | prompt_manager → memory | 5 | 1 | 0 |
| 8 | tools → builtin/ | 7 | 1 | 0 |
| 9 | commands → core/commands | 3 | 1 | 0 |
| 10 | Plugin Contract | 1 | 0 | 6 |
| 11 | Plugin SDK + Host | 3 | 0 | 5 |
| 12 | Tool discovery | 1 | 0 | 2 |
| 13 | SessionManager | 1 | 0 | 6 |
| 14 | AgentKernel + Protocols | 2 | 0 | 3 |
| 15 | UIBridge | 1 | 0 | 0 |
| 16 | app.py 接线 | 0 | 1 | 0 |
| 17 | /plugin 指令 | 1 | 1 | 0 |
| 18 | 示例插件 | 2 | 0 | 0 |
| 19 | 记忆召回 | 1 | 0 | 4 |
| 20 | MCP adapter 骨架 | 1 | 0 | 3 |
| 21 | headless 入口 | 1 | 0 | 0 |
| 22 | 清理 & 收尾 | 0 | 5 | 0 |

**统计**: 22 tasks, ~59 新文件, ~16 修改文件, ~36 新增测试, 预计新增 ~2,200 行代码。
