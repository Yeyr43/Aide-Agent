"""ReflectEngine — 统一记忆反思引擎。

替代 CaptureEngine + EntryManager + PromptUpdater + ContextCompactor。
单一入口 /reflect：LLM 回顾对话 → 更新记忆 + 生成会话总览 → 用户审查 → 原子写入。

P5 重构：砍掉正则引擎，显式/隐式不分，压缩与记忆更新合并。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.locale import t
from core.setup import aide_dir
from core.storage import atomic_write_json, atomic_write_text
from .version import AGENT_ROOT, BACKUPS_DIR, _backup_prompt, _append_version_log, rollback_prompt

logger = logging.getLogger(__name__)

# ── 路径常量 ──────────────────────────────────────────────────────────

SESSIONS_ROOT = aide_dir() / "sessions"

# ── 记忆文件列表 ──────────────────────────────────────────────────────

MEMORY_FILES = {
    "preferences": "preferences.md",
    "workflows": "workflows.md",
    "long_term_memory": "long_term_memory.md",
}

MEMORY_SECTION_HEADERS = {
    "preferences": "## Preferences",
    "workflows": "## Workflows",
    "long_term_memory": "## Long-Term Memory",
}

OVERVIEW_SECTION = "## Session Overview"


# ── ReflectEngine ────────────────────────────────────────────────────


@dataclass
class ReflectResult:
    """一次反思的结果。"""
    overview: str                          # 新的会话总览
    proposed_files: dict[str, str]         # filename → 提议内容
    current_files: dict[str, str]          # filename → 当前内容
    diff: str                              # unified diff（供 UI 展示）
    changes_detected: bool                 # 是否有任何变更


class ReflectEngine:
    """统一记忆反思引擎。

    用法:
        engine = ReflectEngine(provider, agent_root=AGENT_ROOT, sessions_root=SESSIONS_ROOT)
        result = await engine.reflect(session_dir, current_turn)
        # 展示 diff 给用户…
        if user_approved:
            await engine.apply(session_dir, result, current_turn)
    """

    def __init__(self, provider, agent_root: Path | None = None,
                 sessions_root: Path | None = None) -> None:
        self._provider = provider
        self._agent_root = agent_root or AGENT_ROOT
        self._sessions_root = sessions_root or SESSIONS_ROOT
        self._on_cache_flush: callable | None = None

    # ── 主流程 ───────────────────────────────────────────────────────

    async def reflect(self, session_dir: Path,
                      current_turn: int) -> ReflectResult | None:
        """执行反思：读取对话 + 当前记忆 → LLM → 解析 → 返回 diff。

        Args:
            session_dir: 当前会话目录
            current_turn: 当前轮次号

        Returns:
            ReflectResult 或 None（无可反思的内容）
        """
        # 1. 读取当前记忆状态
        current_memory = self._read_current_memory()

        # 2. 确定上次反思的轮次
        last_turn = self._read_reflection_marker(session_dir)

        # 3. 读取新对话
        transcript = self._read_recent_turns(session_dir, last_turn, current_turn)
        if not transcript.strip():
            return None  # 没有新对话，无需反思

        # 4. 读取已有 overview（用于增量更新）
        existing_overview = self._read_existing_overview(session_dir)

        # 5. 调用 LLM
        raw_response = await self._call_llm_for_reflection(
            current_memory, transcript, existing_overview, session_dir,
        )
        if not raw_response:
            return None

        # 6. 解析 LLM 输出
        parsed = self._parse_reflection_output(raw_response, current_memory)

        # 7. 计算 diff
        diff_text = self._compute_diff(current_memory, parsed)

        changes = any(
            parsed.get(k) != current_memory.get(k, "")
            for k in set(list(current_memory.keys()) + list(parsed.keys()))
            if k != "overview"
        ) or parsed.get("overview", "") != existing_overview

        return ReflectResult(
            overview=parsed.get("overview", existing_overview),
            proposed_files={
                "preferences.md": parsed.get("preferences", current_memory.get("preferences.md", "")),
                "workflows.md": parsed.get("workflows", current_memory.get("workflows.md", "")),
                "long_term_memory.md": parsed.get("long_term_memory", current_memory.get("long_term_memory.md", "")),
            },
            current_files={
                "preferences.md": current_memory.get("preferences.md", ""),
                "workflows.md": current_memory.get("workflows.md", ""),
                "long_term_memory.md": current_memory.get("long_term_memory.md", ""),
            },
            diff=diff_text,
            changes_detected=changes,
        )

    async def apply(self, session_dir: Path, result: ReflectResult,
                    current_turn: int) -> None:
        """应用反思结果：备份 → 原子写入 → 更新 marker。

        Args:
            session_dir: 会话目录
            result: reflect() 返回的结果
            current_turn: 当前轮次
        """
        # 写入 overview
        overview_path = session_dir / "overview.md"
        if result.overview:
            atomic_write_text(overview_path, result.overview)

        # 追加 overview.json 检查点
        self._append_checkpoint(session_dir, result.overview, current_turn)

        # 写入记忆文件（只写有变更的）
        for filename, content in result.proposed_files.items():
            if content == result.current_files.get(filename, ""):
                continue  # 无变更，跳过
            prompt_path = self._agent_root / filename
            # 备份
            backup_name = _backup_prompt(prompt_path)
            if backup_name:
                _append_version_log(filename, backup_name)
            # 原子写入
            atomic_write_text(prompt_path, content)

        # 更新反思标记
        self._write_reflection_marker(session_dir, current_turn)

        # 刷新上下文缓存
        if self._on_cache_flush:
            self._on_cache_flush()

        logger.info(f"反思完成: turn={current_turn}")

    # ── 读取当前状态 ─────────────────────────────────────────────────

    def _read_current_memory(self) -> dict[str, str]:
        """读取所有 agent/*.md 文件。"""
        result: dict[str, str] = {}
        for fname in MEMORY_FILES.values():
            path = self._agent_root / fname
            if path.exists():
                try:
                    result[fname] = path.read_text(encoding="utf-8")
                except OSError:
                    result[fname] = ""
            else:
                result[fname] = ""
        return result

    def _read_existing_overview(self, session_dir: Path) -> str:
        """读取已有 overview.md。"""
        path = session_dir / "overview.md"
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                pass
        return ""

    def _read_reflection_marker(self, session_dir: Path) -> int:
        """读取反思标记：从 meta.json 的 last_reflected_turn 字段。
        兼容旧 .reflection_marker 文件（P5 迁移后优先 meta.json）。"""
        # 优先从 meta.json 读取
        meta_path = session_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return meta.get("last_reflected_turn", 0)
            except (json.JSONDecodeError, OSError):
                pass
        # 兼容旧 .reflection_marker
        old_marker = session_dir / ".reflection_marker"
        if old_marker.exists():
            try:
                data = json.loads(old_marker.read_text(encoding="utf-8"))
                return data.get("last_turn", 0)
            except (json.JSONDecodeError, OSError):
                pass
        return 0

    def _write_reflection_marker(self, session_dir: Path, turn: int) -> None:
        """写入反思标记到 meta.json。"""
        meta_path = session_dir / "meta.json"
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        meta["last_reflected_turn"] = turn
        meta["reflected_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(meta_path, meta)

    # ── 读取对话 ──────────────────────────────────────────────────────

    def _read_recent_turns(self, session_dir: Path, since_turn: int,
                           to_turn: int) -> str:
        """读取 [since_turn+1, to_turn] 区间的对话 turn 文件，构建 transcript。"""
        messages_dir = session_dir / "messages"
        if not messages_dir.exists():
            return ""

        parts: list[str] = []
        for turn_num in range(since_turn + 1, to_turn + 1):
            turn_path = messages_dir / f"turn_{turn_num:03d}.json"
            if not turn_path.exists():
                continue
            try:
                data = json.loads(turn_path.read_text(encoding="utf-8"))
                user_text = data.get("user", "")
                assistant_text = data.get("assistant", "")
                # P5: tool_calls 从 messages 内 assistant 消息提取
                msgs = data.get("messages", [])
                tool_names = []
                for msg in (msgs or []):
                    if isinstance(msg, dict):
                        for tc in msg.get("tool_calls", []) or []:
                            name = tc.get("name") or tc.get("function", {}).get("name", "")
                            if name:
                                tool_names.append(name)

                parts.append(f"--- Turn {turn_num} ---")
                parts.append(f"User: {user_text[:600]}")
                if tool_names:
                    parts.append(f"[工具调用: {', '.join(tool_names)}]")
                if assistant_text:
                    parts.append(f"Assistant: {assistant_text[:600]}")
                parts.append("")
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        return "\n".join(parts)

    # ── LLM 调用 ──────────────────────────────────────────────────────

    async def _call_llm_for_reflection(
        self,
        current_memory: dict[str, str],
        transcript: str,
        existing_overview: str,
        session_dir: Path,
    ) -> str | None:
        """调用 LLM 执行反思。"""
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            current_memory, transcript, existing_overview, session_dir,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response_text = ""
            async for event in self._provider.chat_with_tools(messages, []):
                from core.llm_gateway import TextDelta, StreamEnd
                if isinstance(event, TextDelta):
                    response_text += event.content
                elif isinstance(event, StreamEnd):
                    break
        except TypeError:
            logger.exception("ReflectEngine LLM 流处理类型错误")
            return None
        except Exception:
            logger.exception("ReflectEngine LLM 调用失败")
            return None

        return _clean_markdown_response(response_text)

    def _build_system_prompt(self) -> str:
        """构建统一的 system prompt — 指导 LLM 输出结构化记忆条目。"""
        mem_types = {
            "preferences": ("pref", "偏好"),
            "workflows": ("wf", "工作流"),
            "long_term_memory": ("ltm", "长记忆"),
        }
        frontmatter_example = ""
        for key, (prefix, label) in mem_types.items():
            section = MEMORY_SECTION_HEADERS[key]
            frontmatter_example += (
                f"### {section}\n"
                f"---\n"
                f"id: {prefix}_001\n"
                f"created: YYYY-MM-DD\n"
                f"source: YYYYMMDD_HHMMSS/turn_N\n"
                f"---\n"
                f"- [{label}] 具体内容\n\n"
            )

        return f"""{t("mem.reflect_system_intro")}

## 你的任务

你会收到：
1. 当前的三份记忆文件（含结构化的 id/created/source 元数据）
2. 上次反思以来的新对话
3. 已有会话总览

请一次性完成以下所有任务：

### A. 更新会话总览
用 "## Session Overview" 标题，总结对话中的关键话题、决策和结论。

### B-D. 更新记忆文件

每条记忆用 frontmatter + 列表项两层格式：

{frontmatter_example}

**元数据规则**：
- id: 用类型前缀+序号（{", ".join(f"{k}={p}" for k, (p, _) in mem_types.items())}）
- created: 用最早产生该信号的对话日期（ISO 格式）
- source: 用产生该信号的会话和轮次（如 20260803_120000/turn_3）
- 无变更的旧条目**原样输出**（保留原 id、created、source）
- 新条目生成新 id，不与已有条目冲突
- 已失效的条目直接删除（不要出现在输出中）

## 通用规则

- 如果某 section 无需更新，返回 "## Section\\n(无变更)" 或重复现有内容
- 每条记忆最多 40 字（中文）或 40 词（英文）
- 每个文件最多 15 条
- **宁可漏过不可误收** — 不确定时丢弃
- 新信号与旧条目矛盾时，信任新信号，删除旧条目
- 输出格式严格按上例，不要加代码块包裹
- frontmatter 中不要加 weight/deviations 字段（系统自动维护）"""

    def _build_user_prompt(
        self,
        current_memory: dict[str, str],
        transcript: str,
        existing_overview: str,
        session_dir: Path,
    ) -> str:
        """构建 user prompt——展示现有条目及其 id，引导 LLM 保留元数据。"""
        parts: list[str] = []
        session_id = session_dir.name if session_dir else "?"

        # 当前记忆（含结构化 frontmatter）
        parts.append("## 当前记忆文件\n")
        parts.append("（保留 id/created/source 不变的条目，删除失效的，新增的赋予新 id）\n")
        for fname, label in [
            ("preferences.md", "偏好 (Preferences)"),
            ("workflows.md", "工作流 (Workflows)"),
            ("long_term_memory.md", "长记忆 (Long-Term Memory)"),
        ]:
            content = current_memory.get(fname, "").strip()
            content_display = content if content else "(空)"
            parts.append(f"### {label}\n{content_display}\n")

        # 已有 overview
        if existing_overview.strip():
            parts.append(f"## 已有会话总览\n{existing_overview}\n")

        # 新对话 + 来源提示（注入真实 session_id）
        parts.append("## 新对话记录\n")
        parts.append(f"（本次会话 ID: {session_id}。source 字段格式: {session_id}/turn_N）\n")
        parts.append(transcript)

        parts.append("## 要求\n请按 system prompt 中的 frontmatter 格式输出所有 section。")
        return "\n".join(parts)

    # ── 解析 LLM 输出 ─────────────────────────────────────────────────

    def _parse_reflection_output(self, raw: str,
                                  current_memory: dict[str, str]) -> dict[str, str]:
        """解析 LLM 的结构化 Markdown 输出。

        Returns:
            {"overview": str, "preferences": str, "workflows": str, "long_term_memory": str}
        """
        result: dict[str, str] = {
            "overview": "",
            "preferences": current_memory.get("preferences.md", ""),
            "workflows": current_memory.get("workflows.md", ""),
            "long_term_memory": current_memory.get("long_term_memory.md", ""),
        }

        # 按 ## Section 分割
        sections = self._split_sections(raw)

        # 映射 section 标题到 key
        for title, content in sections.items():
            content_stripped = content.strip()
            # 跳过"无变更"标记
            if content_stripped in ("(无变更)", "(no changes)", "(unchanged)"):
                continue

            title_lower = title.lower()
            if "overview" in title_lower or "总览" in title or "overview" in title_lower:
                result["overview"] = f"## {title}\n\n{content_stripped}"
            elif "preference" in title_lower or "偏好" in title:
                result["preferences"] = f"# 偏好\n\n{content_stripped}" if not content_stripped.startswith("#") else content_stripped
            elif "workflow" in title_lower or "工作流" in title:
                result["workflows"] = f"# 工作流\n\n{content_stripped}" if not content_stripped.startswith("#") else content_stripped
            elif "memory" in title_lower or "长记忆" in title or "记忆" in title:
                result["long_term_memory"] = f"# 长记忆\n\n{content_stripped}" if not content_stripped.startswith("#") else content_stripped

        return result

    @staticmethod
    def _split_sections(text: str) -> dict[str, str]:
        """将 Markdown 按 ## 标题分割为 {标题: 内容}。"""
        sections: dict[str, str] = {}
        current_title: str | None = None
        current_lines: list[str] = []

        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                # 保存上一个 section
                if current_title is not None:
                    sections[current_title] = "\n".join(current_lines).strip()
                current_title = stripped[3:].strip()
                current_lines = []
            elif current_title is not None:
                current_lines.append(line)

        # 保存最后一个 section
        if current_title is not None:
            sections[current_title] = "\n".join(current_lines).strip()

        return sections

    # ── Diff ───────────────────────────────────────────────────────────

    def _compute_diff(self, current: dict[str, str],
                      proposed: dict[str, str]) -> str:
        """计算当前和提议之间的 unified diff。"""
        import difflib

        diff_parts: list[str] = []
        for fname, label in [
            ("preferences.md", "偏好"),
            ("workflows.md", "工作流"),
            ("long_term_memory.md", "长记忆"),
        ]:
            old = current.get(fname, "").splitlines(keepends=True)
            new = proposed.get(fname, "").splitlines(keepends=True)
            if old != new:
                diff_lines = list(difflib.unified_diff(
                    old, new,
                    fromfile=f"当前 {label}", tofile=f"提议 {label}",
                ))
                if diff_lines:
                    diff_parts.append("\n".join(diff_lines))

        return "\n\n".join(diff_parts)

    def _append_checkpoint(self, session_dir: Path, overview_md: str,
                           to_turn: int) -> None:
        """追加 overview.json 检查点（去重：同一 to_turn 只保留最新）。"""
        overview_json_path = session_dir / "overview.json"
        checkpoints: list[dict] = []
        if overview_json_path.exists():
            try:
                from core.storage import read_jsonl
                existing = read_jsonl(overview_json_path)
                if isinstance(existing, list):
                    # 去重：移除同一 to_turn 的旧检查点
                    checkpoints = [cp for cp in existing if cp.get("to_turn", 0) != to_turn]
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        checkpoints.append({
            "to_turn": to_turn,
            "compressed_at": datetime.now(timezone.utc).isoformat(),
            "overview_md": overview_md,
        })
        atomic_write_json(overview_json_path, checkpoints)


# ── Markdown 清理（从 compactor.py 移植）──────────────────────────────


def _clean_markdown_response(text: str) -> str:
    """清理 LLM 返回的 Markdown：去掉可能的代码块包裹。"""
    text = text.strip()
    # 去掉 ```markdown ... ``` 包裹
    m = re.match(r'```(?:markdown|md)?\s*\n(.*?)\n```\s*$', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 去掉开头的 ``` 和结尾的 ```
    if text.startswith("```"):
        text = re.sub(r'^```(?:markdown|md)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()
