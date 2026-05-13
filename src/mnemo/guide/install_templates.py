"""Install template engine for Mnemo Guide.

Generates platform-specific and client-specific install command templates
based on the intent and detected client/platform. Uses the real install
commands from the Mnemo distribution.
"""

from __future__ import annotations

from typing import Optional


class InstallTemplateEngine:
    """Generate install command templates for Mnemo.

    Usage::

        engine = InstallTemplateEngine()
        result = engine.generate(platform="macos", client="claude_code")
    """

    # ------------------------------------------------------------------
    # Platform install commands (real commands from Mnemo release)
    # ------------------------------------------------------------------

    _POSIX_INSTALL_CMD = (
        "curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh"
    )
    _WINDOWS_INSTALL_CMD = (
        "irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex"
    )

    _PLATFORM_STEPS: dict[str, dict] = {
        "macos": {
            "title": "macOS 安装 Mnemo",
            "steps": [
                "打开终端（Terminal.app）",
                "运行以下命令安装 Mnemo：",
                f"`{_POSIX_INSTALL_CMD}`",
                "等待安装完成后重启你的 AI 客户端",
            ],
            "commands": [
                {
                    "id": "install-posix",
                    "description": "安装 Mnemo（macOS/Linux）",
                    "command": _POSIX_INSTALL_CMD,
                }
            ],
        },
        "linux": {
            "title": "Linux 安装 Mnemo",
            "steps": [
                "打开终端",
                "运行以下命令安装 Mnemo：",
                f"`{_POSIX_INSTALL_CMD}`",
                "等待安装完成后重启你的 AI 客户端",
            ],
            "commands": [
                {
                    "id": "install-posix",
                    "description": "安装 Mnemo（macOS/Linux）",
                    "command": _POSIX_INSTALL_CMD,
                }
            ],
        },
        "windows": {
            "title": "Windows 安装 Mnemo",
            "steps": [
                "打开 PowerShell（以普通用户身份运行即可）",
                "运行以下命令安装 Mnemo：",
                f"`{_WINDOWS_INSTALL_CMD}`",
                "等待安装完成后重启你的 AI 客户端",
            ],
            "commands": [
                {
                    "id": "install-windows",
                    "description": "安装 Mnemo（Windows）",
                    "command": _WINDOWS_INSTALL_CMD,
                }
            ],
        },
    }

    # ------------------------------------------------------------------
    # Client setup commands
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Client setup commands
    # ------------------------------------------------------------------

    _SETUP_CMD = {"id": "setup-auto", "description": "自动配置所有客户端", "command": "mnemo setup --auto"}

    _CLIENT_SETUP_STEPS: dict[str, dict] = {
        "claude_code": {
            "title": "Claude Code 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 Claude Code",
                "重启 Claude Code",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "Claude Code 的 MCP 配置在 `~/.claude.json`，mnemo setup --auto 会自动添加。",
        },
        "cursor": {
            "title": "Cursor 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 Cursor",
                "重启 Cursor",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "Cursor 的 MCP 配置在 `~/.cursor/mcp.json`，mnemo setup --auto 会自动添加。",
        },
        "codebuddy": {
            "title": "CodeBuddy 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 CodeBuddy",
                "重启 CodeBuddy",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "CodeBuddy 的 MCP 配置在 `~/.codebuddy/.mcp.json`，mnemo setup --auto 会自动添加。",
        },
        "codex": {
            "title": "Codex CLI 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 Codex CLI",
                "重启 Codex CLI",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "Codex CLI 的 MCP 配置在 `~/.codex/config.toml`，mnemo setup --auto 会自动添加。",
        },
        "deepseek": {
            "title": "DeepSeek 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 DeepSeek 客户端",
                "重启你的 AI 客户端",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
        },
        "kimi": {
            "title": "Kimi 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 Kimi 客户端",
                "重启你的 AI 客户端",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
        },
        "windsurf": {
            "title": "Windsurf 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 Windsurf",
                "重启 Windsurf",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "Windsurf 的 MCP 配置在 `~/.codeium/windsurf/mcp_config.json`，mnemo setup --auto 会自动添加。",
        },
        "gemini": {
            "title": "Gemini CLI 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 Gemini CLI",
                "重启 Gemini CLI",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "Gemini CLI 的 MCP 配置在 `~/.gemini/settings.json`，mnemo setup --auto 会自动添加。",
        },
        "copilot": {
            "title": "GitHub Copilot 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 GitHub Copilot CLI",
                "重启 Copilot CLI",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "Copilot CLI 的 MCP 配置在 `~/.copilot/mcp-config.json`，mnemo setup --auto 会自动添加。",
        },
        "qwen": {
            "title": "Qwen Code 接入 Mnemo",
            "steps": [
                "Mnemo 安装完成后，运行 `mnemo setup --auto` 自动配置 Qwen Code",
                "重启 Qwen Code",
                "验证：问你的 Agent 'mnemo 是什么'，看是否能正常回答",
            ],
            "commands": [_SETUP_CMD],
            "note": "Qwen Code 的 MCP 配置在 `~/.qwen/settings.json`，mnemo setup --auto 会自动添加。",
        },
    }

    # ------------------------------------------------------------------
    # Generic install (unknown platform)
    # ------------------------------------------------------------------

    _GENERIC_INSTALL: dict = {
        "title": "Mnemo 安装指南",
        "steps": [
            "请告诉我你的操作系统（macOS / Linux / Windows），我可以给你对应的安装命令。",
            "或者你可以访问官方安装页面获取完整指引：",
            "https://github.com/zhuqingyv/mnemo/releases/latest",
        ],
        "commands": [],
    }

    _GENERIC_CLIENT_SETUP: dict = {
        "title": "Mnemo 客户端接入通用指南",
        "steps": [
            "请告诉我你要接入哪个 AI 客户端，可选：",
            "Claude Code / Cursor / CodeBuddy / Codex CLI / Windsurf / Gemini CLI / Copilot CLI / Qwen Code",
        ],
        "commands": [],
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        platform: str = "unknown",
        client: str = "unknown",
        intent: str = "install",
    ) -> Optional[dict]:
        """Generate install or setup command templates.

        Args:
            platform: One of ``"macos"``, ``"linux"``, ``"windows"``, ``"unknown"``.
            client: Canonical client name or ``"unknown"``.
            intent: ``"install"`` or ``"client_setup"``.

        Returns:
            A dict with ``steps``, ``commands``, and optionally ``note``,
            or ``None`` if no template can be generated.
        """
        # Install intent: need platform
        if intent == "install":
            if platform in self._PLATFORM_STEPS:
                return self._PLATFORM_STEPS[platform]
            return self._GENERIC_INSTALL

        # Client setup intent: need client
        if intent == "client_setup":
            if client in self._CLIENT_SETUP_STEPS:
                return self._CLIENT_SETUP_STEPS[client]
            return self._GENERIC_CLIENT_SETUP

        # For other intents, try to give useful info
        if platform in self._PLATFORM_STEPS:
            return self._PLATFORM_STEPS[platform]

        return None

    def get_generic_install_info(self) -> dict:
        """Return generic install info for use as a fallback."""
        return {
            "steps": [
                "Mnemo 提供跨平台二进制安装，安装后运行 `mnemo setup --auto` 即可自动配置客户端。",
                "macOS / Linux:",
                f"`{self._POSIX_INSTALL_CMD}`",
                "Windows (PowerShell):",
                f"`{self._WINDOWS_INSTALL_CMD}`",
            ],
            "commands": [
                {
                    "id": "install-posix",
                    "description": "macOS / Linux 安装",
                    "command": self._POSIX_INSTALL_CMD,
                },
                {
                    "id": "install-windows",
                    "description": "Windows 安装",
                    "command": self._WINDOWS_INSTALL_CMD,
                },
            ],
        }
