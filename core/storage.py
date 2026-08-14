"""JSON 文件读写 — Write-Actor 并发模型。

所有写操作入队至单一协程顺序执行。
通过 tempfile + os.replace 原子替换确保崩溃一致性。

用法:
    store = JsonStore()                        # 默认 base_dir = ~/.aide
    store = JsonStore(base_dir=Path("/custom"))# 自定义根目录
    data = await store.read(path)              # 读 JSON
    await store.write(path, {"k":"v"})         # 原子写 JSON
    await store.close()                        # 等待队列清空
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from core.setup import aide_dir

logger = logging.getLogger(__name__)


# ── 共享原子写入 ──────────────────────────────────────────────────────


def atomic_write_json(path: Path, data: dict | list) -> None:
    """通过临时文件 + os.replace 实现原子 JSON 写入。

    供 JsonStore 和 Config 共用，避免崩溃时残留损坏文件。
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(parent),
        delete=False,
        prefix=".tmp_",
        suffix=".json",
    ) as tmp:
        tmp.write(json_str)
        tmp.flush()
        tmp_path = tmp.name

    try:
        os.replace(tmp_path, str(path))
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.debug("Failed to unlink temp file %s, skipping cleanup", tmp_path)
        raise


# ── JSONL 追加工具 ─────────────────────────────────────────────────────


def read_jsonl(path: Path) -> list[dict]:
    """读取 JSONL 文件（每行一个 JSON 对象）为 dict 列表。

    兼容旧 JSON 数组格式：如果文件是 `[{...}, ...]` 格式，自动解析为列表。
    文件不存在时返回空列表。
    """
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return []
    # 兼容旧 JSON 数组格式
    if stripped.startswith("["):
        try:
            import json
            data = json.loads(stripped)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    # JSONL 格式：每行一个 JSON 对象
    entries: list[dict] = []
    for line in stripped.split("\n"):
        line = line.strip()
        if line:
            try:
                import json
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def append_jsonl(path: Path, entry: dict) -> None:
    """原子追加一行 JSON 对象到 JSONL 文件。

    通过临时文件 + os.replace 实现原子追加。
    """
    import json
    new_line = json.dumps(entry, ensure_ascii=False) + "\n"

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    # 读现有内容 + 追加 → 原子写回
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(parent),
        delete=False, prefix=".tmp_", suffix=".jsonl",
    ) as tmp:
        tmp.write(existing)
        tmp.write(new_line)
        tmp.flush()
        tmp_path = tmp.name

    try:
        os.replace(tmp_path, str(path))
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def overwrite_jsonl(path: Path, entries: list[dict]) -> None:
    """覆盖写入 JSONL 文件（原子）。"""
    import json
    content = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n" if entries else ""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(parent),
        delete=False, prefix=".tmp_", suffix=".jsonl",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = tmp.name
    try:
        os.replace(tmp_path, str(path))
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """通过临时文件 + os.replace 实现原子文本写入。"""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(parent),
        delete=False, prefix=".tmp_", suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = tmp.name
    try:
        os.replace(tmp_path, str(path))
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class JsonStore:
    """JSON 文件存储 — Write-Actor 模型。

    读操作为同步（内存缓存可加速），写操作入队串行执行。
    每次写入通过临时文件 + os.replace 保证原子性。
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or aide_dir()
        self._queue: asyncio.Queue[tuple[Path, str, asyncio.Event] | None] = asyncio.Queue()
        self._writer_task: asyncio.Task | None = None

    # ── 生命周期 ──────────────────────────────────────────────────

    async def start(self) -> None:
        """启动后台 writer 协程。"""
        if self._writer_task is None:
            self._writer_task = asyncio.create_task(self._writer_loop())

    async def close(self) -> None:
        """等待队列清空并关闭 writer。"""
        if self._writer_task is None:
            return
        await self._queue.put(None)  # 哨兵
        await self._writer_task
        self._writer_task = None

    # ── 公开 API ──────────────────────────────────────────────────

    async def read(self, path: Path) -> dict | list | None:
        """读取 JSON 文件。

        Args:
            path: JSON 文件路径

        Returns:
            解析后的 dict/list，文件不存在或损坏时返回 None
        """
        if not path.exists():
            return None

        try:
            content = path.read_text(encoding="utf-8")
            return json.loads(content)
        except (json.JSONDecodeError, OSError):
            return None

    async def write(self, path: Path, data: dict | list) -> None:
        """异步写入 JSON（入队，不阻塞调用方）。

        Args:
            path: 目标文件路径
            data: 要序列化的 dict 或 list
        """
        if self._writer_task is None:
            raise RuntimeError("JsonStore 未启动，请先调用 start()")

        done = asyncio.Event()
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        await self._queue.put((path, json_str, done))
        await done.wait()  # 等待写入完成（保证数据已落盘）

    # ── 内部实现 ──────────────────────────────────────────────────

    async def _writer_loop(self) -> None:
        """后台 writer：从队列取任务，原子写入。"""
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                break

            path, json_str, done = item
            try:
                await self._atomic_write(path, json_str)
            except OSError:
                logger.exception("JsonStore 原子写入失败: %s", path)
            finally:
                done.set()
                self._queue.task_done()

    @staticmethod
    async def _atomic_write(path: Path, content: str) -> None:
        """通过 atomic_write_json 实现原子写入。"""
        atomic_write_json(path, json.loads(content))
