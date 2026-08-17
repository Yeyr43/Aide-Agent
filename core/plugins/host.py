"""PluginHost — 插件生命周期管理 + 热插拔。

支持两种插件类型：
  - Python 插件（composite/tool/command/provider）：exec_module 加载 Python 入口
  - 技能插件（skill）：SKILL.md 格式，直接作为 ContextProvider 注册
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from dataclasses import dataclass

from .contract import PluginManifest, PluginAPI
from .adapter import PluginFormatDetector, ExtractedHook
from .sdk import PluginEntry
from .slots import SlotRegistry
from .state import PluginStateManager, PluginStatus

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


# ── P7: 外部技能 ContextProvider ────────────────────────────────────────


class ExternalSkillProvider:
    """ContextProvider for external-format skills (Claude Code / OpenClaw).

    通过适配器提取技能内容，按技能名与用户消息的相关性注入 system prompt。
    """

    def __init__(self, name: str, description: str,
                 content: str, references: dict[str, str] | None = None) -> None:
        self._name = name
        self._description = description
        self._content = content
        self._references = references or {}

    async def provide(self, user_msg: str, session_dir) -> str:
        if not user_msg or not self._content:
            return ""
        # 关键词匹配
        msg_lower = user_msg.lower()
        name_lower = self._name.lower()
        if name_lower not in msg_lower and name_lower.replace(":", " ") not in msg_lower:
            return ""
        parts = [f"## 技能: {self._name}\n{self._content}"]
        for ref_name, ref_text in self._references.items():
            parts.append(f"\n### {ref_name}\n{ref_text}")
        return "\n".join(parts)


# ── PluginHost ────────────────────────────────────────────────────────


class PluginHost:
    """插件运行时 — 发现 → 校验 → 加载 → 激活 → 卸载。

    P7: 集成 PluginFormatDetector（Claude Code / OpenClaw / Aide native），
    命名空间隔离，Hook 注册。
    """

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
        self._skill_providers: dict[str, ExternalSkillProvider] = {}
        self._format_detector = PluginFormatDetector()
        self._all_hooks: list[ExtractedHook] = []  # P7: 所有插件的 hooks 合集
        # 显式传 config root：不传会回退到真实 ~/.aide/config/plugin_states.json，
        # 测试（tmp_path 隔离的 Config）会污染真实用户配置，跨进程持久
        self._state_mgr = PluginStateManager(config.aide_root / "config")  # P7: 插件状态管理器

    # ── 发现 ──

    def discover(self) -> list[PluginManifest]:
        """扫描 plugins_dir 下所有子目录，返回发现的 manifest 列表。

        P7: 使用 detect_plugin_format 识别三种格式。
        """
        plugins_dir = self._config.plugins_dir
        if not plugins_dir.exists():
            return []

        manifests: list[PluginManifest] = []
        for entry in sorted(plugins_dir.iterdir()):
            if not entry.is_dir():
                continue

            # P7: 先尝试旧格式（兼容），然后新格式检测
            result = self._format_detector.detect(entry)
            if result is not None:
                fmt, adapter, manifest_v2 = result
                # 转换为旧 PluginManifest 以保持兼容
                manifest = PluginManifest(
                    id=manifest_v2.name,
                    name=manifest_v2.name,
                    version=manifest_v2.version,
                    description=manifest_v2.description,
                    kind="composite" if fmt == "aide_native" else "skill",
                    entry=manifest_v2.aide_python_entry or (
                        "SKILL.md" if fmt in ("claude_code", "openclaw_skill") else "__init__.py"
                    ),
                    root_dir=entry,
                )
                manifests.append(manifest)
                continue

            # Fallback: 旧格式检测
            manifest = PluginManifest.from_dir(entry)
            if manifest is not None:
                manifests.append(manifest)
        return manifests

    # ── 上下文提供者 ──

    def get_context_providers(self) -> list:
        """返回所有已加载插件注册的 ContextProvider（供 ContextPipeline 使用）。"""
        providers: list = []
        # 技能类型的 provider（非 Python 插件）
        for sp in self._skill_providers.values():
            providers.append(sp)
        # Python 插件注册的 context provider
        for info in self._plugins.values():
            if info.api:
                providers.extend(info.api._context_providers)
        return providers

    # ── 加载/卸载 ──

    def _resolve_plugin_dir(self, plugin_id: str, plugins_dir: Path) -> Path | None:
        """解析插件目录：优先按 discover 的 manifest.id 匹配真实目录。

        discover 的 id 来自 SKILL.md 的 name frontmatter，可能与目录名不一致
        （OpenClaw 插件常见：目录 openclaw-agent-browser 但 name=agent-browser）。
        load(m.id) 若按 id 直接拼目录会找不到，必须先回查 manifest 的 root_dir。
        找不到再按目录名兜底（旧行为兼容）。
        """
        for manifest in self.discover():
            if manifest.id == plugin_id:
                return manifest.root_dir
        candidate = (plugins_dir / plugin_id).resolve()
        if str(candidate).startswith(str(plugins_dir)):
            return candidate
        return None

    async def load(self, plugin_id: str) -> PluginInfo | None:
        """加载并激活单个插件。

        P7: 先走 FormatDetector 识别格式（Claude Code / OpenClaw / Aide native），
        再走适配器提取 skills/hooks/commands。Fallback 到旧 PluginManifest.from_dir()。
        """
        # 安全门：不允许路径逃逸
        plugins_dir = self._config.plugins_dir.resolve()
        plugin_dir = self._resolve_plugin_dir(plugin_id, plugins_dir)
        if plugin_dir is None:
            logger.warning(f"拒绝加载插件 {plugin_id}: 路径逃逸或目录不存在")
            return None

        # ── P7: ClawScan 安全预检 ──
        from .security import PluginPreflightCheck
        preflight = PluginPreflightCheck()
        result = await preflight.check(plugin_dir)
        if result.blocked:
            for w in result.warnings:
                if w.level == "error":
                    logger.warning(f"插件 {plugin_id} 安全预检阻止: [{w.category}] {w.message}")
            logger.warning(f"插件 {plugin_id} 未通过安全预检，拒绝加载")
            return None
        if not result.passed:
            for w in result.warnings:
                logger.warning(f"插件 {plugin_id} 安全警告: [{w.category}] {w.message}")

        # ── P7: 优先走格式检测器（识别 .claude-plugin/plugin.json、SKILL.md 等）──
        detected = self._format_detector.detect(plugin_dir)
        if detected is not None:
            fmt, adapter, manifest_v2 = detected

            # 构建 fallback manifest（用于旧 PluginInfo 兼容）
            manifest = PluginManifest(
                id=manifest_v2.name,
                name=manifest_v2.name,
                version=manifest_v2.version,
                description=manifest_v2.description,
                kind="composite" if fmt == "aide_native" else "skill",
                entry=manifest_v2.aide_python_entry or (
                    "SKILL.md" if fmt in ("claude_code", "openclaw_skill") else "__init__.py"
                ),
                root_dir=plugin_dir,
            )

            # 提取 hooks（所有格式通用）
            try:
                extracted_hooks = await adapter.extract_hooks()
                for hook in extracted_hooks:
                    self._all_hooks.append(hook)
            except Exception:
                logger.debug(f"提取 {plugin_id} hooks 失败", exc_info=True)

            # Claude Code / OpenClaw → 外部技能加载
            if fmt in ("claude_code", "openclaw_skill"):
                return await self._load_external_skill(
                    plugin_id, plugin_dir, manifest, adapter,
                )

            # Aide native 格式 → 继续走 Python 加载路径
            if fmt == "aide_native":
                return await self._load_python_plugin(
                    plugin_id, plugin_dir, manifest,
                )

        # ── Fallback: 旧格式检测（aide.plugin.json / 根目录 SKILL.md）──
        manifest = PluginManifest.from_dir(plugin_dir)
        if manifest is None:
            logger.warning(f"插件 {plugin_id} 无有效 manifest")
            return None


        # ── Python 插件类型：exec_module + register(api) ──
        return await self._load_python_plugin(plugin_id, plugin_dir, manifest)

    async def _load_python_plugin(self, plugin_id: str, plugin_dir: Path,
                                   manifest: PluginManifest) -> PluginInfo | None:
        """加载 Python 插件：exec_module → register(api) → 注册 tools/commands/slots。"""
        entry_file = plugin_dir / manifest.entry
        if not entry_file.exists():
            logger.warning(f"插件 {plugin_id} 入口文件 {manifest.entry} 不存在")
            return None

        # 安全门：world-writable（仅 POSIX，Windows 权限模型不同）
        if sys.platform != "win32":
            try:
                if entry_file.stat().st_mode & 0o002:
                    logger.warning(f"拒绝加载插件 {plugin_id}: 文件可被他人写入")
                    return None
            except OSError:
                pass

        # DISABLED 插件不加载：必须在 exec_module / 注册工具命令之前拦截，
        # 否则"已禁用"的插件仍会被注册、可被调用
        if self._state_mgr.get(plugin_id).status == PluginStatus.DISABLED:
            logger.info(f"插件 {plugin_id} 已禁用，跳过加载")
            return None

        # 导入模块
        try:
            module_name = f"aide_plugin_{manifest.id}"
            spec = importlib.util.spec_from_file_location(module_name, entry_file)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception:
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
        except Exception:
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
        # P7: 自动验证依赖（DISABLED 已在加载前拦截，走到这里不可能是 DISABLED）
        if api._requirements:
            self._state_mgr.verify_requirements(plugin_id, api._requirements)
        else:
            self._state_mgr.set_status(plugin_id, PluginStatus.READY)
        logger.info(f"插件已加载: {plugin_id}")
        return info

    async def _load_external_skill(
        self, plugin_id: str, plugin_dir: Path,
        manifest: PluginManifest, adapter,
    ) -> PluginInfo | None:
        """P7: 加载外部格式技能（Claude Code / OpenClaw）。

        通过适配器提取 skills，注册为带命名空间前缀的工具+命令+上下文。
        """
        from core.tools import ToolDefinition
        from core.commands import CommandDefinition

        # DISABLED 外部技能不加载（同样要早于工具/命令注册拦截）
        if self._state_mgr.get(plugin_id).status == PluginStatus.DISABLED:
            logger.info(f"外部技能 {plugin_id} 已禁用，跳过加载")
            return None

        try:
            extracted_skills = await adapter.extract_skills()
        except Exception:
            logger.exception(f"提取 {plugin_id} 技能失败")
            return None

        if not extracted_skills:
            logger.warning(f"外部插件 {plugin_id} 无有效技能")
            return None

        api = PluginAPI(plugin_id)

        for skill in extracted_skills:
            skill_name = skill.name
            # 命名空间隔离: skill_name → plugin-name:skill-name
            ns_skill_name = f"{plugin_id}:{skill_name}"

            # ── 1. 注册 //plugin:skill 命令 ──
            cmd_name = f"//{ns_skill_name}"
            skill_desc = skill.description[:80]
            skill_content = skill.content
            skill_refs = skill.references or {}

            async def make_skill_handler(content=skill_content, desc=skill_desc,
                                         refs=None, sname=ns_skill_name):
                refs = refs or {}
                async def handler(app, args: str) -> str:
                    parts = [f"## {sname}\n\n{desc}\n\n{content}"]
                    for ref_name, ref_text in refs.items():
                        parts.append(f"\n### {ref_name}\n{ref_text}")
                    return "\n".join(parts)
                return handler

            api.register_command(CommandDefinition(
                name=cmd_name,
                description=f"技能: {skill_desc[:50]}...",
                handler=await make_skill_handler(),
            ))
            self._command_registry.register(api._commands[-1])

            # ── 2. 注册 skill_<plugin>:<name> 工具 ──
            tool_name = f"skill_{plugin_id}_{skill_name}"

            async def make_skill_executor(content=skill_content, refs=None):
                refs = refs or {}
                async def execute(arguments: dict) -> str:
                    parts = [content]
                    for ref_name, ref_text in refs.items():
                        parts.append(f"\n### {ref_name}\n{ref_text}")
                    return "\n\n".join(parts)
                return execute

            api.register_tool(ToolDefinition(
                name=tool_name,
                description=(
                    f"调用「{ns_skill_name}」技能获取详细指导。"
                    f"当任务涉及 {skill_desc[:100]} 时使用此工具。"
                ),
                parameters={"type": "object", "properties": {}, "required": []},
                execute=await make_skill_executor(),
            ))
            self._tool_registry.register(api._tools[-1])

            # ── 3. 注册为 ContextProvider ──
            provider = ExternalSkillProvider(
                ns_skill_name, skill_desc, skill_content, skill_refs,
            )
            # 只登记到 _skill_providers（get_context_providers 会返回它）。
            # 曾同时 append 到 api._context_providers → 同一 provider 被返回两次，
            # 技能上下文被双份注入（此前被"传空 user_msg 注入失效"掩盖）。
            self._skill_providers[ns_skill_name] = provider

        # ── P7: 提取命令（Claude Code commands/*.md）──
        try:
            extracted_commands = await adapter.extract_commands()
            for cmd in extracted_commands:
                cmd_name = f"//{plugin_id}:{cmd.name}"
                cmd_content = cmd.content
                cmd_desc = cmd.description[:80] or cmd.name
                api.register_command(CommandDefinition(
                    name=cmd_name,
                    description=f"命令: {cmd_desc[:50]}...",
                    handler=(lambda c=cmd_content, d=cmd_desc, n=cmd_name:
                             (lambda app, args: f"## {n}\n\n{d}\n\n{c}"))(),
                ))
                self._command_registry.register(api._commands[-1])
        except Exception:
            logger.debug(f"提取 {plugin_id} 命令失败", exc_info=True)

        # ── P7: 提取 MCP servers（.mcp.json）──
        try:
            mcp_servers = await adapter.extract_mcp_servers()
            for server in mcp_servers:
                if isinstance(server, dict) and server.get("command"):
                    logger.info(
                        f"外部插件 {plugin_id} 声明了 MCP server: {server.get('name', server['command'])} — "
                        f"请手动在 ~/.aide/mcp/servers.json 中配置"
                    )
        except Exception:
            logger.debug(f"提取 {plugin_id} MCP 配置失败", exc_info=True)

        # ── P7: 提取 settings（settings.json）──
        try:
            settings = await adapter.extract_settings()
            if settings:
                logger.info(
                    f"外部插件 {plugin_id} 包含 settings.json: "
                    f"{', '.join(settings.keys())} — 请手动检查配置"
                )
        except Exception:
            logger.debug(f"提取 {plugin_id} settings 失败", exc_info=True)

        info = PluginInfo(manifest=manifest, loaded=True, api=api, module=None)
        self._plugins[plugin_id] = info
        # P7: DISABLED 已在加载前拦截，走到这里直接置 READY
        self._state_mgr.set_status(plugin_id, PluginStatus.READY)
        logger.info(
            f"外部技能已加载: {plugin_id} — "
            f"{len(extracted_skills)} 技能, 格式={adapter.FINGERPRINT}"
        )
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

        # 移除插件注册的工具
        if info.api:
            for tool in info.api._tools:
                self._tool_registry.unregister(tool.name)

        # 移除技能 provider（含外部技能的 ns_skill_name 键）
        self._skill_providers.pop(plugin_id, None)
        ns_prefix = f"{plugin_id}:"
        leaked_keys = [k for k in self._skill_providers if k.startswith(ns_prefix)]
        for k in leaked_keys:
            self._skill_providers.pop(k, None)

        # 卸载 Python 模块
        module_name = f"aide_plugin_{plugin_id}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        # P7: 清理插件状态
        self._state_mgr.remove(plugin_id)

        logger.info(f"插件已卸载: {plugin_id}")
        return True

    async def reload(self, plugin_id: str) -> PluginInfo | None:
        await self.unload(plugin_id)
        return await self.load(plugin_id)

    def list_loaded(self) -> list[PluginInfo]:
        return list(self._plugins.values())

    def is_loaded(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins

    def count(self) -> int:
        """返回已加载插件数量。"""
        return len(self._plugins)

    def get_hooks(self) -> list:
        """P7: 返回所有已注册 hooks（含适配器提取的 + Python 插件注册的）。"""
        hooks = list(self._all_hooks)
        for info in self._plugins.values():
            if info.api and info.api._hooks:
                for h in info.api._hooks:
                    hooks.append(ExtractedHook(**h) if isinstance(h, dict) else h)
        return hooks

    @property
    def state_manager(self) -> PluginStateManager:
        """P7: 插件状态管理器。"""
        return self._state_mgr

    async def enable_plugin(self, plugin_id: str) -> None:
        """P7: 启用插件 — 置 READY，未加载时重新加载。"""
        self._state_mgr.enable(plugin_id)
        if plugin_id not in self._plugins:
            await self.load(plugin_id)

    async def disable_plugin(self, plugin_id: str) -> None:
        """P7: 禁用插件 — 置 DISABLED 并从内存卸载（工具/命令取消注册）。"""
        await self.unload(plugin_id)  # 已加载则完整卸载；未加载则无操作
        self._state_mgr.disable(plugin_id)

    @property
    def slot_registry(self) -> SlotRegistry:
        return self._slot_registry
