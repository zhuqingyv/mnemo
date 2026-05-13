"""Tests for the Install Template Engine."""

import pytest
from mnemo.guide.install_templates import InstallTemplateEngine


@pytest.fixture
def engine() -> InstallTemplateEngine:
    return InstallTemplateEngine()


class TestInstallTemplates:
    def test_macos_install(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(platform="macos", intent="install")
        assert result is not None
        assert "macOS" in result["title"]
        assert len(result["commands"]) > 0

    def test_windows_install(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(platform="windows", intent="install")
        assert result is not None
        assert "Windows" in result["title"]
        assert len(result["commands"]) > 0

    def test_linux_install(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(platform="linux", intent="install")
        assert result is not None
        assert "Linux" in result["title"]
        assert len(result["commands"]) > 0

    def test_unknown_platform(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(platform="unknown", intent="install")
        assert result is not None
        assert result["title"] == "Mnemo 安装指南"

    def test_claude_code_setup(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(client="claude_code", intent="client_setup")
        assert result is not None
        assert "Claude Code" in result["title"]

    def test_cursor_setup(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(client="cursor", intent="client_setup")
        assert result is not None
        assert "Cursor" in result["title"]

    def test_codebuddy_setup(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(client="codebuddy", intent="client_setup")
        assert result is not None
        assert "CodeBuddy" in result["title"]

    def test_codex_setup(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(client="codex", intent="client_setup")
        assert result is not None
        assert "Codex" in result["title"]

    def test_deepseek_setup(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(client="deepseek", intent="client_setup")
        assert result is not None
        assert "DeepSeek" in result["title"]

    def test_unknown_client(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(client="unknown", intent="client_setup")
        assert result is not None
        assert result["title"] == "Mnemo 客户端接入通用指南"

    def test_no_match_intent(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(intent="mnemo_overview")
        assert result is None

    def test_result_structure(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(platform="macos", intent="install")
        assert result is not None
        assert "title" in result
        assert "steps" in result
        assert "commands" in result
        assert isinstance(result["commands"], list)
        assert isinstance(result["steps"], list)

    def test_commands_have_required_fields(self, engine: InstallTemplateEngine) -> None:
        result = engine.generate(platform="macos", intent="install")
        assert result is not None
        for cmd in result["commands"]:
            assert "id" in cmd
            assert "description" in cmd
            assert "command" in cmd
