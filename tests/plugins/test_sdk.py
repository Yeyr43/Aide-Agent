"""Tests for Plugin SDK — define_plugin decorator."""

import pytest
from core.plugins.sdk import define_plugin
from core.plugins.contract import PluginAPI


class TestDefinePlugin:
    def test_sets_plugin_id_attribute(self):
        @define_plugin("my-test-plugin")
        def register(api: PluginAPI):
            pass

        assert register.__aide_plugin_id__ == "my-test-plugin"

    def test_returns_same_function(self):
        @define_plugin("test")
        def register(api: PluginAPI):
            return 42

        assert register(None) == 42

    def test_different_plugins_have_different_ids(self):
        @define_plugin("plugin-a")
        def reg_a(api: PluginAPI):
            pass

        @define_plugin("plugin-b")
        def reg_b(api: PluginAPI):
            pass

        assert reg_a.__aide_plugin_id__ == "plugin-a"
        assert reg_b.__aide_plugin_id__ == "plugin-b"

    def test_preserves_function_metadata(self):
        @define_plugin("test")
        def my_plugin(api: PluginAPI):
            """My plugin docstring."""
            pass

        assert my_plugin.__name__ == "my_plugin"
        assert my_plugin.__doc__ == "My plugin docstring."
