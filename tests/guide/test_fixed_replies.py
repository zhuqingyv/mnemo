"""Tests for the Fixed Reply Handler."""

from mnemo.guide.fixed_replies import is_fixed_reply_question, IDENTITY_REPLY, OFF_TOPIC_REPLY


class TestFixedReplyDetection:
    def test_identity_question(self) -> None:
        result = is_fixed_reply_question("你是谁")
        assert result is not None
        assert "Mnemo Guide" in result

    def test_capability_question(self) -> None:
        result = is_fixed_reply_question("你有什么能力")
        assert result is not None
        assert "Mnemo Guide" in result

    def test_what_can_you_do(self) -> None:
        result = is_fixed_reply_question("你能做什么")
        assert result is not None
        assert "Mnemo Guide" in result

    def test_open_web_question(self) -> None:
        result = is_fixed_reply_question("你能打开网页吗")
        assert result is not None
        assert "Mnemo Guide" in result

    def test_read_memory_question(self) -> None:
        result = is_fixed_reply_question("你能读取我的私人记忆吗")
        assert result is not None
        assert "Mnemo Guide" in result

    def test_execute_command_question(self) -> None:
        result = is_fixed_reply_question("你能执行命令吗")
        assert result is not None
        assert "Mnemo Guide" in result

    def test_write_code_question(self) -> None:
        result = is_fixed_reply_question("帮我写代码")
        assert result is not None
        assert "只回答 Mnemo" in result

    def test_react_question(self) -> None:
        result = is_fixed_reply_question("帮我写React代码")
        assert result is not None
        assert "只回答 Mnemo" in result

    def test_chrome_question(self) -> None:
        result = is_fixed_reply_question("Chrome 是什么")
        assert result is not None
        assert "只回答 Mnemo" in result

    def test_google_question(self) -> None:
        result = is_fixed_reply_question("Google 是什么")
        assert result is not None
        assert "只回答 Mnemo" in result

    def test_game_question(self) -> None:
        result = is_fixed_reply_question("帮我写一个小游戏")
        assert result is not None
        assert "只回答 Mnemo" in result

    def test_weather_question(self) -> None:
        result = is_fixed_reply_question("深圳天气如何")
        assert result is not None

    def test_url_question(self) -> None:
        result = is_fixed_reply_question("你能打开 https://github.com 看看吗")
        assert result is not None
        assert "不能打开网页" in result

    def test_mnemo_question_not_fixed(self) -> None:
        result = is_fixed_reply_question("Mnemo 是什么")
        assert result is None

    def test_install_question_not_fixed(self) -> None:
        result = is_fixed_reply_question("怎么安装")
        assert result is None

    def test_identity_reply_content(self) -> None:
        assert "Mnemo Guide" in IDENTITY_REPLY
        assert "本地说明书助手" in IDENTITY_REPLY
        assert "不会读取你的私人记忆" in IDENTITY_REPLY

    def test_off_topic_reply_content(self) -> None:
        assert "只回答 Mnemo" in OFF_TOPIC_REPLY
        assert "Mnemo 是什么" in OFF_TOPIC_REPLY
        assert "怎么安装" in OFF_TOPIC_REPLY
