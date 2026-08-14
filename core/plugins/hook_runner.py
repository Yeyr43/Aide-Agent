"""HookRunner — 生命周期钩子运行时。

对标 Claude Code 的 9 种事件 hooks：
  SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
  PermissionRequest, Stop, PreCompact, SubagentStop, Notification

Matcher 语法：
  "write_file"                  → 精确匹配工具名
  "run_shell|write_file"        → 管道（匹配任一）
  "*"                           → 所有工具
  "mcp_*"                       → 通配符前缀
  "write_file(*.py)"            → 文件扩展名匹配
  "run_shell(command=rm *)"     → 参数键值匹配
  "re:^write_"                  → 正则

退出码语义（对标 Claude Code）：
  0   → 成功，允许继续
  2   → 阻止（仅 PreToolUse/PermissionRequest），stderr 传给 LLM
  其他 → 非阻塞警告，记录日志
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .adapter import ExtractedHook

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class HookDefinition:
    """一条 hook 配置。"""
    event: str            # "SessionStart" | "UserPromptSubmit" | ...
    matcher: str = "*"    # 工具名匹配模式
    type: str = "command" # "command"
    command: str = ""     # shell 命令
    timeout: int = 60     # 超时（秒）
    env: dict | None = None  # 额外环境变量


@dataclass
class HookContext:
    """Hook 执行上下文 — 运行时注入的信息。"""
    event: str
    tool_name: str = ""
    tool_args: dict | None = None
    file_path: str = ""
    session_id: str = ""
    turn: int = 0
    user_prompt: str = ""
    plugin_name: str = ""


@dataclass
class HookResult:
    """Hook 执行结果。"""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    decision: str = "allow"       # "allow" | "block" | "approve"
    modified_input: dict | None = None  # PreToolUse 修改后的工具参数


# ── Matcher 编译器 ────────────────────────────────────────────────────────


class MatcherCompiler:
    """编译 matcher 字符串为可执行的模式匹配器。"""

    # 文件扩展名匹配: tool_name(*.ext)
    _EXT_RE = re.compile(r'^(\w+)\((\*\.\w+)\)$')
    # 参数键值匹配: tool_name(key=pattern)
    _KEYVAL_RE = re.compile(r'^(\w+)\((\w+)=(.+)\)$')

    def compile(self, pattern: str) -> "Matcher":
        """编译单个 pattern 为 Matcher 对象。

        一个 pattern 可能包含多个子模式（管道分隔）。
        """
        if "|" in pattern:
            # 管道：拆为多个子 Matcher
            subs = [self._compile_single(p.strip()) for p in pattern.split("|")]
            return OrMatcher(subs)

        return self._compile_single(pattern)

    def _compile_single(self, pattern: str) -> "Matcher":
        """编译单个（非管道）pattern。"""
        # 正则
        if pattern.startswith("re:"):
            try:
                return RegexMatcher(re.compile(pattern[3:]))
            except re.error:
                logger.warning(f"无效正则 hook matcher: {pattern}")
                return NoopMatcher()

        # 全匹配
        if pattern == "*":
            return AnyMatcher()

        # 通配符前缀
        if pattern.endswith("*") and "(" not in pattern:
            prefix = pattern[:-1]
            return PrefixMatcher(prefix)

        # 文件扩展名: tool(*.ext)
        m = self._EXT_RE.match(pattern)
        if m:
            return ExtMatcher(m.group(1), m.group(2)[1:])  # strip * from *.ext

        # 参数键值: tool(key=pattern)
        m = self._KEYVAL_RE.match(pattern)
        if m:
            return KeyValMatcher(m.group(1), m.group(2), m.group(3))

        # 默认：精确匹配工具名
        return ExactMatcher(pattern)


class Matcher:
    """模式匹配器基类。"""
    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        raise NotImplementedError


class AnyMatcher(Matcher):
    """匹配所有工具。"""
    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        return True


class NoopMatcher(Matcher):
    """永不匹配。"""
    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        return False


class ExactMatcher(Matcher):
    """精确匹配工具名。"""
    def __init__(self, name: str):
        self._name = name

    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        return tool_name == self._name


class PrefixMatcher(Matcher):
    """通配符前缀匹配。"""
    def __init__(self, prefix: str):
        self._prefix = prefix

    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        return tool_name.startswith(self._prefix)


class RegexMatcher(Matcher):
    """正则匹配。"""
    def __init__(self, pattern: re.Pattern):
        self._pattern = pattern

    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        return bool(self._pattern.search(tool_name))


class ExtMatcher(Matcher):
    """文件扩展名匹配: write_file(*.py) → tool=write_file 且路径以 .py 结尾。"""
    def __init__(self, tool_name: str, ext: str):
        self._tool = tool_name
        self._ext = ext  # ".py", ".js" etc.

    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        if tool_name != self._tool:
            return False
        path = file_path or (arguments or {}).get("file_path", "") or (arguments or {}).get("filepath", "")
        return str(path).endswith(self._ext)


class KeyValMatcher(Matcher):
    """参数键值匹配: run_shell(command=rm *) → tool=run_shell 且 command 参数包含 rm。"""
    def __init__(self, tool_name: str, key: str, pattern: str):
        self._tool = tool_name
        self._key = key
        self._pattern = pattern.strip()

    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        if tool_name != self._tool:
            return False
        args = arguments or {}
        val = args.get(self._key, "")
        if self._key == "command":
            val = val or args.get("cmd", "")
        if not isinstance(val, str):
            return False
        # pattern 以 * 结尾时做前缀/包含匹配
        if self._pattern.endswith("*"):
            p = self._pattern[:-1].strip()
            return p in val
        return val == self._pattern


class OrMatcher(Matcher):
    """管道：任一子 Matcher 匹配即可。"""
    def __init__(self, matchers: list[Matcher]):
        self._matchers = matchers

    def matches(self, tool_name: str, file_path: str = "",
                arguments: dict | None = None) -> bool:
        return any(m.matches(tool_name, file_path, arguments) for m in self._matchers)


# ── HookRunner ────────────────────────────────────────────────────────────


class HookRunner:
    """执行生命周期 hooks 并收集结果。

    用法:
        runner = HookRunner(hooks)
        ctx = HookContext(event="PreToolUse", tool_name="run_shell", ...)
        results = await runner.run("PreToolUse", ctx)
        for r in results:
            if r.decision == "block":
                return "阻止执行"
    """

    def __init__(self, hooks: list[ExtractedHook] | None = None) -> None:
        self._hooks: dict[str, list[tuple[Matcher, ExtractedHook]]] = defaultdict(list)
        self._compiler = MatcherCompiler()
        if hooks:
            for hook in hooks:
                self.register(hook)

    def register(self, hook: ExtractedHook) -> None:
        """注册一个 hook（编译 matcher）。"""
        matcher = self._compiler.compile(hook.matcher)
        self._hooks[hook.event].append((matcher, hook))

    def register_raw(self, event: str, matcher_str: str,
                     command: str, timeout: int = 60) -> None:
        """注册原始 hook（无 ExtractedHook 对象时使用）。"""
        hook = ExtractedHook(
            event=event, matcher=matcher_str,
            type="command", command=command, timeout=timeout,
        )
        self.register(hook)

    def load_from_dicts(self, hook_dicts: list[dict]) -> None:
        """从 dict 列表批量加载 hooks（来自 hooks.json）。"""
        for entry in hook_dicts:
            if isinstance(entry, dict) and entry.get("event"):
                self.register(ExtractedHook(
                    event=entry["event"],
                    matcher=entry.get("matcher", "*"),
                    type=entry.get("type", "command"),
                    command=entry.get("command", ""),
                    timeout=entry.get("timeout", 60),
                ))

    # ── 执行 ──────────────────────────────────────────────────────────

    async def run(self, event: str, ctx: HookContext) -> list[HookResult]:
        """执行某事件的所有匹配 hooks。

        Args:
            event: 事件名
            ctx: 运行时上下文

        Returns:
            HookResult 列表。调用者负责检查 decision 和 exit_code。
        """
        matched = self._match(event, ctx)
        if not matched:
            return []

        tasks = [self._execute_one(hook, ctx) for hook in matched]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: list[HookResult] = []
        for r in results:
            if isinstance(r, HookResult):
                output.append(r)
            elif isinstance(r, Exception):
                logger.debug(f"Hook 执行异常: {r}")
                output.append(HookResult(exit_code=1, stderr=str(r)))
        return output

    def _match(self, event: str, ctx: HookContext) -> list[ExtractedHook]:
        """返回匹配该事件 + 上下文的所有 hooks。"""
        event_hooks = self._hooks.get(event, [])
        matched = []
        for matcher, hook in event_hooks:
            if matcher.matches(ctx.tool_name, ctx.file_path, ctx.tool_args):
                matched.append(hook)
        return matched

    async def _execute_one(self, hook: ExtractedHook,
                           ctx: HookContext) -> HookResult:
        """执行单个 hook — asyncio subprocess 运行 shell 命令。"""
        env = self._build_env(ctx)

        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=hook.timeout,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
                stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                return HookResult(exit_code=124, stderr="hook timeout")

            result = HookResult(
                exit_code=proc.returncode or 0,
                stdout=stdout,
                stderr=stderr,
            )

            # 解析 JSON 输出（对标 Claude Code v2.0.10+）
            parsed = self._parse_json_output(stdout)
            if parsed:
                result.decision = parsed.get("decision", "allow")
                result.modified_input = parsed.get("modifiedInput")

            return result

        except OSError as e:
            return HookResult(exit_code=127, stderr=f"hook command failed: {e}")

    # ── 环境变量 ──────────────────────────────────────────────────────

    @staticmethod
    def _build_env(ctx: HookContext) -> dict:
        """注入环境变量（对标 Claude Code hooks 变量规范）。"""
        env = os.environ.copy()

        if ctx.tool_name:
            env["TOOL_NAME"] = ctx.tool_name
        if ctx.tool_args:
            env["TOOL_ARGS"] = json.dumps(ctx.tool_args, ensure_ascii=False)
        # Claude Code 兼容别名
        if ctx.tool_name:
            env["CLAUDE_TOOL_NAME"] = ctx.tool_name
        if ctx.tool_args:
            env["CLAUDE_TOOL_ARGS"] = json.dumps(ctx.tool_args, ensure_ascii=False)

        if ctx.file_path:
            env["FILE_PATH"] = ctx.file_path
        if ctx.session_id:
            env["SESSION_ID"] = ctx.session_id
        env["TURN"] = str(ctx.turn)
        env["USER_PROMPT"] = ctx.user_prompt or ""
        # Claude Code 兼容别名
        env["CLAUDE_USER_PROMPT"] = ctx.user_prompt or ""

        env["PLUGIN_NAME"] = ctx.plugin_name
        if ctx.session_id:
            env["PROJECT_DIR"] = str(Path.cwd())

        return env

    # ── JSON 输出解析 ─────────────────────────────────────────────────

    @staticmethod
    def _parse_json_output(stdout: str) -> dict | None:
        """尝试从 hook stdout 中解析 JSON 指令。

        对标 Claude Code v2.0.10+:
          {"decision": "block", "reason": "..."}
          {"decision": "approve"}
          {"decision": "allow", "modifiedInput": {...}}
        """
        if not stdout:
            return None
        try:
            data = json.loads(stdout)
            if isinstance(data, dict) and "decision" in data:
                return data
        except json.JSONDecodeError:
            pass
        return None


# ── 便捷函数 ──────────────────────────────────────────────────────────────


def check_hook_results(results: list[HookResult]) -> tuple[bool, str, dict | None]:
    """检查 hook 执行结果，返回 (should_proceed, message, modified_input)。

    - exit_code 2 → 阻止
    - decision "block" → 阻止
    - 其他 → 允许

    如果多个 hooks 有 modifiedInput，最后一个生效。
    """
    for r in results:
        if r.exit_code == 2 or r.decision == "block":
            msg = r.stderr or r.stdout or "hook 阻止了此操作"
            return False, msg, None

    # 收集修改后的输入
    modified = None
    for r in results:
        if r.modified_input:
            modified = r.modified_input

    return True, "", modified
