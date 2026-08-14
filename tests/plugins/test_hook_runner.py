"""测试 hook_runner.py — MatcherCompiler + HookRunner + 完整管线。"""

import asyncio
import pytest

from core.plugins.hook_runner import (
    HookRunner, HookDefinition, HookContext, HookResult,
    MatcherCompiler, check_hook_results,
    AnyMatcher, ExactMatcher, PrefixMatcher, RegexMatcher,
    ExtMatcher, KeyValMatcher, OrMatcher, NoopMatcher,
)
from core.plugins.adapter import ExtractedHook


# ── Matcher 测试 ───────────────────────────────────────────────────────────

class TestMatcherCompiler:
    """测试 MatcherCompiler 的所有语法。"""

    def setup_method(self):
        self.compiler = MatcherCompiler()

    # 精确匹配
    def test_exact_match(self):
        m = self.compiler.compile("write_file")
        assert isinstance(m, ExactMatcher)
        assert m.matches("write_file")
        assert not m.matches("read_file")
        assert not m.matches("write_file_extra")

    # 管道
    def test_pipe_or_match(self):
        m = self.compiler.compile("write_file|read_file")
        assert isinstance(m, OrMatcher)
        assert m.matches("write_file")
        assert m.matches("read_file")
        assert not m.matches("run_shell")

    # 全匹配
    def test_any_match(self):
        m = self.compiler.compile("*")
        assert isinstance(m, AnyMatcher)
        assert m.matches("anything")
        assert m.matches("write_file")
        assert m.matches("")

    # 通配符前缀
    def test_prefix_match(self):
        m = self.compiler.compile("mcp_*")
        assert isinstance(m, PrefixMatcher)
        assert m.matches("mcp_github_search")
        assert m.matches("mcp_filesystem_read")
        assert not m.matches("skill_test")
        assert not m.matches("mcp")  # 无下划线后缀

    # 文件扩展名
    def test_ext_match(self):
        m = self.compiler.compile("write_file(*.py)")
        assert isinstance(m, ExtMatcher)
        assert m.matches("write_file", file_path="/src/main.py")
        assert m.matches("write_file", arguments={"file_path": "/app/test.py"})
        assert not m.matches("write_file", file_path="/src/main.js")
        assert not m.matches("read_file", file_path="/src/main.py")

    def test_ext_match_with_filepath_alias(self):
        """filepath 作为 file_path 的别名。"""
        m = self.compiler.compile("write_file(*.py)")
        assert m.matches("write_file", arguments={"filepath": "/app/test.py"})

    # 参数键值
    def test_keyval_match(self):
        m = self.compiler.compile("run_shell(command=rm *)")
        assert isinstance(m, KeyValMatcher)
        assert m.matches("run_shell", arguments={"command": "rm -rf /tmp"})
        assert m.matches("run_shell", arguments={"command": "rm file.txt"})
        assert not m.matches("run_shell", arguments={"command": "ls -la"})
        assert not m.matches("write_file", arguments={"command": "rm -rf"})

    def test_keyval_match_cmd_alias(self):
        """cmd 作为 command 的别名。"""
        m = self.compiler.compile("run_shell(command=rm *)")
        assert m.matches("run_shell", arguments={"cmd": "rm -rf /"})

    def test_keyval_match_exact(self):
        """不带 * 的精确匹配。"""
        m = self.compiler.compile("run_shell(command=ls)")
        assert m.matches("run_shell", arguments={"command": "ls"})
        assert not m.matches("run_shell", arguments={"command": "ls -la"})

    # 正则
    def test_regex_match(self):
        m = self.compiler.compile("re:^write_")
        assert isinstance(m, RegexMatcher)
        assert m.matches("write_file")
        assert m.matches("write_text")
        assert not m.matches("read_file")

    def test_invalid_regex_fallback(self):
        """无效正则 fallback 到 NoopMatcher。"""
        m = self.compiler.compile("re:[invalid")
        assert isinstance(m, NoopMatcher)

    # 单管道元素
    def test_single_pipe_simplifies(self):
        """单个元素的管道应简化。"""
        m = self.compiler.compile("write_file")
        assert isinstance(m, ExactMatcher)  # 不是 OrMatcher

    # 复杂管道
    def test_complex_pipe(self):
        m = self.compiler.compile("write_file(*.py)|read_file|mcp_*")
        assert isinstance(m, OrMatcher)
        assert m.matches("write_file", file_path="test.py")
        assert m.matches("read_file")
        assert m.matches("mcp_github_search")
        assert not m.matches("run_shell")


# ── HookRunner 测试 ────────────────────────────────────────────────────────

class TestHookRunner:
    """测试 HookRunner 执行管线。"""

    @pytest.fixture
    def runner(self):
        return HookRunner()

    @pytest.fixture
    def ctx(self):
        return HookContext(
            event="PreToolUse",
            tool_name="write_file",
            tool_args={"file_path": "/tmp/test.py"},
            file_path="/tmp/test.py",
        )

    def test_register_and_match(self, runner, ctx):
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="write_file",
            type="command", command='echo "matched"',
        ))
        matched = runner._match("PreToolUse", ctx)
        assert len(matched) == 1
        assert matched[0].command == 'echo "matched"'

    def test_no_match(self, runner, ctx):
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="read_file",
            type="command", command="echo nope",
        ))
        matched = runner._match("PreToolUse", ctx)
        assert len(matched) == 0

    def test_different_event_not_matched(self, runner):
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command="echo test",
        ))
        ctx = HookContext(event="PostToolUse", tool_name="read_file")
        matched = runner._match("PostToolUse", ctx)
        assert len(matched) == 0

    @pytest.mark.asyncio
    async def test_execute_hook(self, runner):
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command="echo hello",
        ))
        ctx = HookContext(event="PreToolUse", tool_name="test")
        results = await runner.run("PreToolUse", ctx)
        assert len(results) == 1
        assert results[0].exit_code == 0
        assert "hello" in results[0].stdout

    @pytest.mark.asyncio
    async def test_execute_hook_failure(self, runner):
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command="exit 1",
        ))
        ctx = HookContext(event="PreToolUse", tool_name="test")
        results = await runner.run("PreToolUse", ctx)
        assert results[0].exit_code == 1

    @pytest.mark.asyncio
    async def test_hook_timeout(self, runner):
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command="sleep 10",
            timeout=1,
        ))
        ctx = HookContext(event="PreToolUse", tool_name="test")
        results = await runner.run("PreToolUse", ctx)
        assert results[0].exit_code == 124  # timeout

    @pytest.mark.asyncio
    async def test_json_output_decision_block(self, runner, tmp_path):
        """stdout 输出 JSON 可覆盖 decision。"""
        import sys, json
        # Write a wrapper script to stdout the JSON
        script = tmp_path / "_block_hook.py"
        script.write_text(
            'import json\n'
            'print(json.dumps({"decision": "block", "reason": "dangerous"}))\n'
        )
        cmd = f'"{sys.executable}" "{script}"'
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command=cmd,
        ))
        ctx = HookContext(event="PreToolUse", tool_name="test")
        results = await runner.run("PreToolUse", ctx)
        assert results[0].decision == "block"

    @pytest.mark.asyncio
    async def test_json_output_modified_input(self, runner, tmp_path):
        """stdout JSON 可包含 modifiedInput。"""
        import sys, json
        script = tmp_path / "_modify_hook.py"
        script.write_text(
            'import json\n'
            'print(json.dumps({"decision": "allow", '
            '"modifiedInput": {"command": "safe"}}))\n'
        )
        cmd = f'"{sys.executable}" "{script}"'
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command=cmd,
        ))
        ctx = HookContext(event="PreToolUse", tool_name="test")
        results = await runner.run("PreToolUse", ctx)
        assert results[0].modified_input == {"command": "safe"}

    @pytest.mark.asyncio
    async def test_invalid_json_ignored(self, runner):
        """非 JSON stdout 不影响 decision。"""
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command="echo just plain text",
        ))
        ctx = HookContext(event="PreToolUse", tool_name="test")
        results = await runner.run("PreToolUse", ctx)
        assert results[0].decision == "allow"  # default

    @pytest.mark.asyncio
    async def test_multiple_hooks(self, runner):
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="write_file",
            type="command", command="echo first",
        ))
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="write_file|read_file",
            type="command", command="echo second",
        ))
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="run_shell",
            type="command", command="echo third",
        ))
        ctx = HookContext(event="PreToolUse", tool_name="write_file")
        results = await runner.run("PreToolUse", ctx)
        # 前两个匹配，第三个不匹配
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_env_injection(self, runner):
        import platform
        if platform.system() == "Windows":
            cmd = 'python -c "import os; print(os.environ.get(\\"TOOL_NAME\\", \\"\\"))"'
        else:
            cmd = 'echo TOOL=$TOOL_NAME FILE=$FILE_PATH'
        runner.register(ExtractedHook(
            event="PreToolUse", matcher="*",
            type="command", command=cmd,
        ))
        ctx = HookContext(
            event="PreToolUse", tool_name="write_file",
            file_path="/tmp/test.py", session_id="sess_001", turn=5,
        )
        results = await runner.run("PreToolUse", ctx)
        if platform.system() == "Windows":
            assert "write_file" in results[0].stdout
        else:
            assert "TOOL=write_file" in results[0].stdout

    # ── load_from_dicts ────────────────────────────────────────────────

    def test_load_from_dicts(self, runner):
        hook_dicts = [
            {"event": "SessionStart", "matcher": "*", "command": "echo start"},
            {"event": "PreToolUse", "matcher": "write_file", "command": "echo check"},
        ]
        runner.load_from_dicts(hook_dicts)
        ctx = HookContext(event="SessionStart", tool_name="")
        matched = runner._match("SessionStart", ctx)
        assert len(matched) == 1


# ── check_hook_results 测试 ───────────────────────────────────────────────

class TestCheckHookResults:
    """测试 check_hook_results 辅助函数。"""

    def test_all_allow(self):
        results = [
            HookResult(exit_code=0, decision="allow"),
            HookResult(exit_code=0, decision="allow"),
        ]
        ok, msg, modified = check_hook_results(results)
        assert ok is True
        assert msg == ""
        assert modified is None

    def test_exit_code_2_blocks(self):
        results = [
            HookResult(exit_code=0),
            HookResult(exit_code=2, stderr="blocked!"),
        ]
        ok, msg, _ = check_hook_results(results)
        assert ok is False
        assert "blocked" in msg

    def test_decision_block_blocks(self):
        results = [HookResult(exit_code=0, decision="block", stderr="nope")]
        ok, msg, _ = check_hook_results(results)
        assert ok is False

    def test_modified_input_last_wins(self):
        results = [
            HookResult(modified_input={"a": 1}),
            HookResult(modified_input={"b": 2}),
        ]
        ok, _, modified = check_hook_results(results)
        assert ok is True
        assert modified == {"b": 2}

    def test_empty_results(self):
        ok, msg, modified = check_hook_results([])
        assert ok is True
