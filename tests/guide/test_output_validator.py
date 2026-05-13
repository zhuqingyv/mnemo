"""Tests for the Output Validator."""

import pytest
from mnemo.guide.model.validator import OutputValidator


@pytest.fixture
def validator() -> OutputValidator:
    return OutputValidator()


class TestValidOutputs:
    def test_valid_mnemo_answer(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "Mnemo 是一个本地 AI 记忆层，为 Agent 提供可沉淀和可检索的记忆。",
            "Mnemo 是什么",
            "mnemo_overview",
        )
        assert valid is True
        assert reason is None

    def test_valid_install_answer(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "安装 Mnemo 只需要运行 curl 命令。配置完成后重启客户端即可。",
            "怎么安装",
            "install",
        )
        assert valid is True
        assert reason is None

    def test_valid_short_answer(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "我是 Mnemo Guide。",
            "你是谁",
            "identity",
        )
        assert valid is True
        assert reason is None


class TestRejectedOutputs:
    def test_empty_output(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate("", "你是谁", "identity")
        assert valid is False
        assert reason == "输出为空"

    def test_claims_internet_access(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "我可以联网搜索更多信息。Mnemo 是一种记忆工具。",
            "你能做什么",
            "capability",
        )
        assert valid is False
        assert reason is not None

    def test_claims_shell_access(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "我可以帮你执行命令。Mnemo 安装很简单。",
            "你能执行命令吗",
            "capability",
        )
        assert valid is False
        assert reason is not None

    def test_claims_read_files(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "我可以读取你的本地文件。",
            "你能读取文件吗",
            "capability",
        )
        assert valid is False
        assert reason is not None

    def test_no_mnemo_mention(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "You should use a different tool for this task.",
            "怎么安装",
            "install",
        )
        assert valid is False
        assert reason is not None

    def test_contains_unrelated_code(self, validator: OutputValidator) -> None:
        # Python code patterns should be rejected
        valid, reason = validator.validate(
            "Mnemo is great.\n```python\ndef hello():\n    return 'world'\n```",
            "帮我写代码",
            "off_topic",
        )
        assert valid is False
        assert reason is not None

    def test_too_long_output(self, validator: OutputValidator) -> None:
        long_output = "Mnemo " * 1000
        valid, reason = validator.validate(
            long_output, "Mnemo 是什么", "mnemo_overview"
        )
        assert valid is False
        assert "输出过长" in reason

    def test_repetitive_output(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "Mnemo is great. " * 50,
            "Mnemo 是什么",
            "mnemo_overview",
        )
        assert valid is False


class TestEdgeCases:
    def test_whitespace_only(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate("   \n  ", "你是谁", "identity")
        assert valid is False

    def test_contains_mnemo_but_also_banned(self, validator: OutputValidator) -> None:
        valid, reason = validator.validate(
            "Mnemo 可以帮你联网查询信息。",
            "你能联网吗",
            "capability",
        )
        assert valid is False
