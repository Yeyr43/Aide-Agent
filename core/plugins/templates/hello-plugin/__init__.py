"""Hello Plugin — Aide 示例插件（工具 + 命令 + 上下文提供者）。"""

from core.plugins.sdk import define_plugin
from core.plugins.contract import PluginAPI
from core.tools import ToolDefinition
from core.commands import CommandDefinition


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


async def hello_command(app, args: str) -> str:
    """插件注册的命令 — //hello。"""
    name = args.strip() or "World"
    return f"## {name} 你好！👋\n\n来自 **Hello Plugin** 的问候。\n可以通过 `/plugin unload hello-plugin` 卸载此命令。"


@define_plugin("hello-plugin")
def register(api: PluginAPI) -> None:
    # 注册工具
    api.register_tool(ToolDefinition(
        name="hello",
        description="向指定名字打招呼（示例插件工具）",
        parameters=HELLO_SCHEMA,
        execute=hello_execute,
    ))

    # 注册命令（自动加 // 前缀 → //hello）
    api.register_command(CommandDefinition(
        name="hello",
        description="来自 Hello Plugin 的问候命令",
        handler=hello_command,
    ))

    # 注册上下文提供者
    class HelloProvider:
        async def provide(self, user_msg: str, session_dir) -> str:
            return "（系统提示：Hello Plugin 已激活，可使用 //hello 命令）"

    api.register_context_provider(HelloProvider())
