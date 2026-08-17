"""Tests for core.commands.builtin.settings_handlers — language/API/model management."""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

from core.commands.builtin.settings_handlers import (
    _resolve_edit_name,
    _list_display,
    _api_result_to_config,
    _parse_ctx,
    handle_language,
    handle_model,
    handle_api,
)


class TestResolveEditName:
    @patch("core.commands.builtin.settings_handlers.Config")
    def test_explicit_name(self, mock_cfg):
        assert _resolve_edit_name("myapi") == "myapi"

    @patch("core.commands.builtin.settings_handlers.Config")
    def test_empty_falls_back_to_active(self, mock_cfg):
        mock_cfg.get_active_api_name.return_value = "default"
        assert _resolve_edit_name("") == "default"


class TestListDisplay:
    @patch("core.commands.builtin.settings_handlers.Config")
    def test_empty_list(self, mock_cfg):
        mock_cfg.list_api_configs.return_value = {}
        mock_cfg.get_active_api_name.return_value = ""
        result = _list_display()
        assert "empty" in result.lower() or "无" in result

    @patch("core.commands.builtin.settings_handlers.Config")
    def test_with_configs(self, mock_cfg):
        mock_cfg.list_api_configs.return_value = {
            "openai": {"provider": "openai", "model": "gpt-4o"},
            "ollama": {"provider": "ollama", "model": "llama3"},
        }
        mock_cfg.get_active_api_name.return_value = "openai"
        result = _list_display()
        assert "openai" in result
        assert "ollama" in result


class TestApiResultToConfig:
    def test_extracts_fields(self):
        result = _api_result_to_config({
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "sk-xxx",
            "base_url": "https://api.openai.com",
            "supports_vision": True,
            "thinking": True,
            "extra_field": "should be ignored",
        })
        assert result == {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "sk-xxx",
            "base_url": "https://api.openai.com",
            "supports_vision": True,
            "thinking": True,
        }


class TestParseCtx:
    def test_valid_int(self):
        assert _parse_ctx({"context_window": "128000"}) == 128000

    def test_int_value(self):
        assert _parse_ctx({"context_window": 64000}) == 64000

    def test_empty_yields_default(self):
        assert _parse_ctx({"context_window": ""}) == 128000

    def test_missing_yields_default(self):
        assert _parse_ctx({}) == 128000

    def test_invalid_yields_default(self):
        assert _parse_ctx({"context_window": "not_a_number"}) == 128000


class TestHandleLanguage:
    @pytest.mark.asyncio
    async def test_unknown_language(self):
        """Unsupported language returns error."""
        app = MagicMock()
        result = await handle_language(app, "fr")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_empty_args_shows_usage(self):
        app = MagicMock()
        result = await handle_language(app, "")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_zh_language(self):
        app = MagicMock()
        with patch("core.locale.set_locale") as mock_set:
            with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
                mock_cfg.load_settings.return_value = {}
                mock_cfg.save_settings = MagicMock()
                result = await handle_language(app, "zh")
                mock_set.assert_called_once_with("zh")
                assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_en_language(self):
        app = MagicMock()
        with patch("core.locale.set_locale") as mock_set:
            with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
                mock_cfg.load_settings.return_value = {}
                mock_cfg.save_settings = MagicMock()
                result = await handle_language(app, "en")
                mock_set.assert_called_once_with("en")
                assert isinstance(result, str)


class TestHandleModel:
    @pytest.mark.asyncio
    async def test_no_args_no_configs(self):
        app = MagicMock()
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {}
            result = await handle_model(app, "")
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_unknown_api_name(self):
        app = MagicMock()
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai", "model": "gpt-4o"},
            }
            result = await handle_model(app, "unknown")
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        app = MagicMock()
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai"},
            }
            result = await handle_model(app, "unknown delete")
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_list_with_configs(self):
        app = MagicMock()
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai", "model": "gpt-4o"},
            }
            mock_cfg.get_active_api_name.return_value = "openai"
            result = await handle_model(app, "")  # no args = list
            assert isinstance(result, str)
            assert "openai" in result


class TestHandleApiAdd:
    """回归：/api add 子命令必须真正创建配置（曾只落在 list_empty 提示自指）。"""

    async def test_add_creates_config(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.api_config_exists.return_value = False
            mock_cfg.get_active_api_name.return_value = ""
            mock_cfg.save_api_config = MagicMock()
            mock_cfg.set_active_api_name = MagicMock()
            result = await handle_api(MagicMock(), "add deepseek deepseek deepseek-chat sk-xxx")
            assert "deepseek" in result
            name, cfg = mock_cfg.save_api_config.call_args[0]
            assert name == "deepseek"
            assert cfg["model"] == "deepseek-chat"
            assert cfg["api_key"] == "sk-xxx"
            assert cfg["base_url"] == ""
            mock_cfg.set_active_api_name.assert_called_once_with("deepseek")

    async def test_add_with_base_url_keeps_existing_active(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.api_config_exists.return_value = False
            mock_cfg.get_active_api_name.return_value = "existing"
            mock_cfg.save_api_config = MagicMock()
            mock_cfg.set_active_api_name = MagicMock()
            await handle_api(MagicMock(), "add myapi openai gpt-4o key https://api.openai.com/v1")
            _, cfg = mock_cfg.save_api_config.call_args[0]
            assert cfg["base_url"] == "https://api.openai.com/v1"
            mock_cfg.set_active_api_name.assert_not_called()

    async def test_add_missing_args_shows_usage(self):
        with patch("core.commands.builtin.settings_handlers.Config"):
            result = await handle_api(MagicMock(), "add onlyname")
            assert "用法" in result

    async def test_add_existing_name_rejects(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.api_config_exists.return_value = True
            result = await handle_api(MagicMock(), "add myapi openai gpt-4o key")
            assert "已存在" in result


def _api_screen_result(name="openai", provider="openai", model="gpt-4o",
                       api_key="sk-1", base_url="", ctx="96000"):
    return {
        "name": name,
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "supports_vision": True,
        "context_window": ctx,
    }


class TestHandleApiNoArg:
    """/api 无参数 → 打开配置页保存。"""

    async def test_screen_cancelled_returns_empty(self):
        app = MagicMock()
        app.open_api_config_screen = AsyncMock(return_value=None)
        with patch("core.commands.builtin.settings_handlers.Config"):
            assert await handle_api(app, "") == ""

    async def test_screen_saved_sets_active_when_none(self):
        app = MagicMock()
        app.open_api_config_screen = AsyncMock(return_value=_api_screen_result())
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = ""
            mock_cfg.save_api_config = MagicMock()
            mock_cfg.set_active_api_name = MagicMock()
            mock_cfg.load_settings.return_value = {}
            mock_cfg.save_settings = MagicMock()
            result = await handle_api(app, "")
            mock_cfg.save_api_config.assert_called_once()
            mock_cfg.set_active_api_name.assert_called_once_with("openai")
            saved = mock_cfg.save_settings.call_args[0][0]
            assert saved["app"]["context_window"] == 96000
            assert "已保存" in result

    async def test_screen_saved_keeps_existing_active(self):
        app = MagicMock()
        app.open_api_config_screen = AsyncMock(return_value=_api_screen_result())
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = "existing"
            mock_cfg.save_api_config = MagicMock()
            mock_cfg.set_active_api_name = MagicMock()
            mock_cfg.load_settings.return_value = {}
            mock_cfg.save_settings = MagicMock()
            await handle_api(app, "")
            mock_cfg.set_active_api_name.assert_not_called()


class TestHandleApiList:
    async def test_list_subcommand(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai", "model": "gpt-4o"},
            }
            mock_cfg.get_active_api_name.return_value = "openai"
            result = await handle_api(MagicMock(), "list")
            assert "openai" in result
            assert "gpt-4o" in result


class TestHandleApiDelete:
    async def test_delete_missing_name(self):
        with patch("core.commands.builtin.settings_handlers.Config"):
            result = await handle_api(MagicMock(), "delete")
            assert "用法" in result

    async def test_delete_not_found(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.api_config_exists.return_value = False
            result = await handle_api(MagicMock(), "delete foo")
            assert "未找到" in result

    async def test_delete_active_clears_active(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.api_config_exists.return_value = True
            mock_cfg.get_active_api_name.return_value = "foo"
            mock_cfg.delete_api_config = MagicMock()
            mock_cfg.load_settings.return_value = {"active_api": "foo"}
            mock_cfg.save_settings = MagicMock()
            result = await handle_api(MagicMock(), "delete foo")
            mock_cfg.delete_api_config.assert_called_once_with("foo")
            saved = mock_cfg.save_settings.call_args[0][0]
            assert saved["active_api"] == ""
            assert "已删除" in result

    async def test_delete_non_active_keeps_active(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.api_config_exists.return_value = True
            mock_cfg.get_active_api_name.return_value = "other"
            mock_cfg.delete_api_config = MagicMock()
            result = await handle_api(MagicMock(), "delete foo")
            assert "已删除" in result
            mock_cfg.save_settings.assert_not_called()


class TestHandleApiEdit:
    async def test_edit_no_name_no_active(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = ""
            result = await handle_api(MagicMock(), "edit")
            assert "暂无" in result

    async def test_edit_not_found(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = "foo"
            mock_cfg.api_config_exists.return_value = False
            result = await handle_api(MagicMock(), "edit foo")
            assert "未找到" in result

    async def test_edit_uses_active_name_when_omitted(self):
        app = MagicMock()
        app.open_api_config_screen = AsyncMock(return_value=None)
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = "active-api"
            mock_cfg.api_config_exists.return_value = True
            result = await handle_api(app, "edit")
            assert result == ""
            app.open_api_config_screen.assert_called_once_with(edit_name="active-api")

    async def test_edit_cancelled(self):
        app = MagicMock()
        app.open_api_config_screen = AsyncMock(return_value=None)
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = "foo"
            mock_cfg.api_config_exists.return_value = True
            result = await handle_api(app, "edit foo")
            assert result == ""
            app.open_api_config_screen.assert_called_once_with(edit_name="foo")

    async def test_edit_same_name_no_rename(self):
        app = MagicMock()
        app.open_api_config_screen = AsyncMock(
            return_value=_api_screen_result(name="foo"))
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = "foo"
            mock_cfg.api_config_exists.return_value = True
            mock_cfg.save_api_config = MagicMock()
            mock_cfg.delete_api_config = MagicMock()
            result = await handle_api(app, "edit foo")
            mock_cfg.save_api_config.assert_called_once()
            mock_cfg.delete_api_config.assert_not_called()
            assert "已保存" in result

    async def test_edit_renamed_updates_active(self):
        app = MagicMock()
        app.open_api_config_screen = AsyncMock(
            return_value=_api_screen_result(name="newname"))
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.get_active_api_name.return_value = "foo"
            mock_cfg.api_config_exists.return_value = True
            mock_cfg.save_api_config = MagicMock()
            mock_cfg.delete_api_config = MagicMock()
            mock_cfg.set_active_api_name = MagicMock()
            result = await handle_api(app, "edit foo")
            mock_cfg.delete_api_config.assert_called_once_with("foo")
            mock_cfg.set_active_api_name.assert_called_once_with("newname")
            assert "已保存" in result


class TestHandleApiUnknownSub:
    async def test_unknown_subcommand_falls_to_list_empty(self):
        """未知子命令落入最终兜底（list_empty）。"""
        with patch("core.commands.builtin.settings_handlers.Config"):
            result = await handle_api(MagicMock(), "bogus")
            assert "暂无" in result


class TestHandleModelDelete:
    async def test_delete_active_clears_active(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai", "model": "gpt-4o"},
            }
            mock_cfg.get_active_api_name.return_value = "openai"
            mock_cfg.delete_api_config = MagicMock()
            mock_cfg.load_settings.return_value = {"active_api": "openai"}
            mock_cfg.save_settings = MagicMock()
            result = await handle_model(MagicMock(), "openai delete")
            mock_cfg.delete_api_config.assert_called_once_with("openai")
            saved = mock_cfg.save_settings.call_args[0][0]
            assert saved["active_api"] == ""
            assert "已删除" in result

    async def test_delete_non_active(self):
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai"},
            }
            mock_cfg.get_active_api_name.return_value = "other"
            mock_cfg.delete_api_config = MagicMock()
            result = await handle_model(MagicMock(), "openai delete")
            assert "已删除" in result
            mock_cfg.save_settings.assert_not_called()


class TestHandleModelSwitch:
    async def test_switch_without_provider_attr(self):
        app = MagicMock()
        del app.provider  # 无 provider 属性 → 跳过运行时更新
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai", "model": "gpt-4o"},
            }
            mock_cfg.set_active_api_name = MagicMock()
            result = await handle_model(app, "openai")
            mock_cfg.set_active_api_name.assert_called_once_with("openai")
            assert "已切换" in result

    async def test_switch_updates_provider(self):
        app = MagicMock()
        app.provider = None
        app.kernel = MagicMock()
        app.refresh_status_bar_model = MagicMock()
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai", "model": "gpt-4o"},
            }
            mock_cfg.set_active_api_name = MagicMock()
            with patch("core.config.Config.load",
                       return_value=SimpleNamespace(
                           llm=SimpleNamespace(model="gpt-4o", provider="openai"))):
                with patch("core.llm_gateway.create_provider",
                           return_value="fake_provider") as mk:
                    result = await handle_model(app, "openai")
        mk.assert_called_once()
        assert app.provider == "fake_provider"
        # 状态栏模型/API 名经 refresh_status_bar_model(model=..., api_name=...) 更新
        app.refresh_status_bar_model.assert_called_once_with(
            model="gpt-4o", api_name="openai")
        app.kernel.set_provider.assert_called_once_with("fake_provider")
        assert "已切换" in result

    async def test_switch_provider_failure(self):
        app = MagicMock()
        app.provider = None
        with patch("core.commands.builtin.settings_handlers.Config") as mock_cfg:
            mock_cfg.list_api_configs.return_value = {
                "openai": {"provider": "openai", "model": "gpt-4o"},
            }
            mock_cfg.set_active_api_name = MagicMock()
            with patch("core.config.Config.load",
                       return_value=SimpleNamespace(
                           llm=SimpleNamespace(model="gpt-4o", provider="openai"))):
                with patch("core.llm_gateway.create_provider",
                           side_effect=RuntimeError("boom")):
                    result = await handle_model(app, "openai")
        assert "初始化失败" in result
