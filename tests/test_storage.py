"""Tests for JsonStore — Write-Actor JSON file storage."""

import json
import pytest
import asyncio
from pathlib import Path

from core.storage import JsonStore


class TestJsonStoreInit:
    def test_default_base_dir(self):
        store = JsonStore()
        assert store._base_dir == Path.home() / ".aide"

    def test_custom_base_dir(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        assert store._base_dir == tmp_path


class TestJsonStoreLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_writer_task(self):
        store = JsonStore()
        assert store._writer_task is None
        await store.start()
        assert store._writer_task is not None
        await store.close()

    @pytest.mark.asyncio
    async def test_close_stops_writer(self):
        store = JsonStore()
        await store.start()
        assert store._writer_task is not None
        await store.close()
        assert store._writer_task is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        """Close on an unstarted store should not error."""
        store = JsonStore()
        await store.close()  # should not raise


class TestJsonStoreRead:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "test.json"
            path.write_text('{"key": "value"}', encoding="utf-8")
            result = await store.read(path)
            assert result == {"key": "value"}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "missing.json"
            result = await store.read(path)
            assert result is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_read_corrupt_json(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "bad.json"
            path.write_text("not valid json", encoding="utf-8")
            result = await store.read(path)
            assert result is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_read_list(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "list.json"
            path.write_text('[1, 2, 3]', encoding="utf-8")
            result = await store.read(path)
            assert result == [1, 2, 3]
        finally:
            await store.close()


class TestJsonStoreWrite:
    @pytest.mark.asyncio
    async def test_write_creates_file(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "new.json"
            await store.write(path, {"hello": "world"})
            assert path.exists()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data == {"hello": "world"}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "data.json"
            path.write_text('{"version": 1}', encoding="utf-8")
            await store.write(path, {"version": 2})
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data == {"version": 2}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "deep" / "nested" / "file.json"
            await store.write(path, {"x": 1})
            assert path.exists()
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_write_no_temp_residue(self, tmp_path):
        """After write, no .tmp_ files should remain."""
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "data.json"
            await store.write(path, {"clean": True})
            tmp_files = list(tmp_path.glob(".tmp_*"))
            assert len(tmp_files) == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_write_raises_if_not_started(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        with pytest.raises(RuntimeError, match="未启动"):
            await store.write(tmp_path / "x.json", {"a": 1})

    @pytest.mark.asyncio
    async def test_write_list_data(self, tmp_path):
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            path = tmp_path / "items.json"
            await store.write(path, [1, 2, 3])
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data == [1, 2, 3]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_concurrent_writes(self, tmp_path):
        """Multiple concurrent writes should all succeed."""
        store = JsonStore(base_dir=tmp_path)
        await store.start()
        try:
            async def write_one(n: int):
                await store.write(tmp_path / f"file_{n}.json", {"n": n})

            tasks = [write_one(i) for i in range(10)]
            await asyncio.gather(*tasks)

            for i in range(10):
                path = tmp_path / f"file_{i}.json"
                assert path.exists()
                data = json.loads(path.read_text(encoding="utf-8"))
                assert data == {"n": i}
        finally:
            await store.close()
