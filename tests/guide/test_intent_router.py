"""Tests for the Intent Router."""

import pytest
from mnemo.guide.intent_router import IntentRouter
from mnemo.guide.types import GuideIntent


@pytest.fixture
def router() -> IntentRouter:
    return IntentRouter()


class TestFixedReplyDetection:
    def test_identity_you_are_who(self, router: IntentRouter) -> None:
        result = router.route("你是谁")
        assert result.is_fixed_reply is True
        assert result.intent == "identity"
        assert result.fixed_reply_text is not None
        assert "Mnemo Guide" in result.fixed_reply_text

    def test_identity_capability(self, router: IntentRouter) -> None:
        result = router.route("你有什么能力")
        assert result.is_fixed_reply is True
        assert result.intent == "identity"

    def test_can_you_open_web(self, router: IntentRouter) -> None:
        result = router.route("你能打开网页吗")
        assert result.is_fixed_reply is True

    def test_can_you_read_memory(self, router: IntentRouter) -> None:
        result = router.route("你能读取我的私人记忆吗")
        assert result.is_fixed_reply is True

    def test_off_topic_react(self, router: IntentRouter) -> None:
        result = router.route("帮我写React代码")
        assert result.is_fixed_reply is True
        assert result.intent == "off_topic"

    def test_off_topic_game(self, router: IntentRouter) -> None:
        result = router.route("帮我写一个小游戏")
        assert result.is_fixed_reply is True
        assert result.intent == "off_topic"

    def test_off_topic_weather(self, router: IntentRouter) -> None:
        result = router.route("今天深圳天气如何")
        assert result.is_fixed_reply is True

    def test_off_topic_chrome(self, router: IntentRouter) -> None:
        result = router.route("Chrome 是什么")
        assert result.is_fixed_reply is True

    def test_off_topic_google(self, router: IntentRouter) -> None:
        result = router.route("Google 是什么")
        assert result.is_fixed_reply is True

    def test_url_in_question(self, router: IntentRouter) -> None:
        result = router.route("你能打开 https://example.com 看看吗")
        assert result.is_fixed_reply is True


class TestMnemoIntents:
    def test_overview(self, router: IntentRouter) -> None:
        result = router.route("Mnemo 是什么")
        assert result.intent == "mnemo_overview"
        assert result.is_fixed_reply is False

    def test_install_intent(self, router: IntentRouter) -> None:
        result = router.route("怎么安装")
        assert result.intent == "install"

    def test_install_with_platform(self, router: IntentRouter) -> None:
        result = router.route("Mac 怎么安装")
        assert result.intent == "install"
        assert result.platform == "macos"

    def test_install_with_windows(self, router: IntentRouter) -> None:
        result = router.route("Windows 安装")
        assert result.intent == "install"
        assert result.platform == "windows"

    def test_install_with_linux(self, router: IntentRouter) -> None:
        result = router.route("Ubuntu 安装")
        assert result.intent == "install"
        assert result.platform == "linux"


class TestClientDetection:
    def test_claude_code(self, router: IntentRouter) -> None:
        result = router.route("Claude Code 怎么接入")
        assert result.intent == "client_setup"
        assert result.client == "claude_code"

    def test_cursor(self, router: IntentRouter) -> None:
        result = router.route("Cursor 怎么接入")
        assert result.intent == "client_setup"
        assert result.client == "cursor"

    def test_codebuddy(self, router: IntentRouter) -> None:
        result = router.route("CodeBuddy 怎么接入")
        assert result.intent == "client_setup"
        assert result.client == "codebuddy"

    def test_codex(self, router: IntentRouter) -> None:
        result = router.route("Codex CLI 怎么接入")
        assert result.intent == "client_setup"
        assert result.client == "codex"

    def test_deepseek(self, router: IntentRouter) -> None:
        result = router.route("DeepSeek Agent 怎么接入")
        assert result.intent == "client_setup"
        assert result.client == "deepseek"

    def test_client_setup_no_specific(self, router: IntentRouter) -> None:
        result = router.route("怎么接入")
        assert result.intent == "client_setup"
        assert result.client is None

    def test_client_with_platform(self, router: IntentRouter) -> None:
        result = router.route("Claude Code on Mac")
        assert result.intent == "client_setup"
        assert result.client == "claude_code"
        assert result.platform == "macos"


class TestConceptIntents:
    def test_mcp_explain(self, router: IntentRouter) -> None:
        result = router.route("MCP 注入是什么")
        assert result.intent == "mcp_explain"

    def test_global_prompt_explain(self, router: IntentRouter) -> None:
        result = router.route("全局提示词注入是什么")
        assert result.intent == "global_prompt_explain"

    def test_troubleshooting(self, router: IntentRouter) -> None:
        result = router.route("Agent 没有记忆怎么办")
        assert result.intent == "troubleshooting"

    def test_verify(self, router: IntentRouter) -> None:
        result = router.route("如何验证 Mnemo 是否生效")
        assert result.intent == "verify"

    def test_privacy(self, router: IntentRouter) -> None:
        result = router.route("隐私安全")
        assert result.intent == "privacy_security"

    def test_empty_question(self, router: IntentRouter) -> None:
        result = router.route("")
        assert result.intent == "unknown"

    def test_unknown_question(self, router: IntentRouter) -> None:
        result = router.route("asdfghjk12345")
        assert result.intent == "unknown"
