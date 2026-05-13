"""Mock model provider for testing the Mnemo Guide.

Returns canned responses based on keywords in the prompt. Useful for
integration tests that need to exercise the full guide pipeline without
a real model.
"""

from typing import Optional

from mnemo.guide.model.provider import LocalModelProvider


class MockModelProvider(LocalModelProvider):
    """Mock model that returns keyword-triggered canned responses.

    Usage::

        provider = MockModelProvider()
        assert await provider.is_available() is True
        answer = await provider.generate("怎么安装 Mnemo")
    """

    def __init__(self, responses: Optional[dict[str, str]] = None) -> None:
        self.responses = responses or {}
        self.call_count = 0

    async def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        self.call_count += 1

        # Check custom responses first
        for keyword, response in self.responses.items():
            if keyword in prompt:
                return response

        # Built-in canned responses by topic
        if "安装" in prompt:
            return (
                "根据 Mnemo 说明书的指引，安装 Mnemo 只需要一行命令：\n\n"
                "macOS / Linux:\n"
                "curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh\n\n"
                "Windows (PowerShell):\n"
                "irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex"
            )

        if "接入" in prompt or "配置" in prompt:
            return (
                "根据公共知识卡片，接入 Mnemo 需要先安装，然后运行：\n\n"
                "mnemo setup --auto\n\n"
                "这会自动检测你的 AI 客户端（Claude Code / Cursor / CodeBuddy 等）"
                "并写入 MCP 配置。完成后重启客户端即可生效。"
            )

        if "是什么" in prompt or "what is" in prompt.lower():
            return (
                "Mnemo 是一个本地 AI 记忆层，为 Agent 提供可沉淀、"
                "可检索、可复用的记忆系统。Agent 在完成任务后可以将经验、"
                "决策、踩坑记录存入 Mnemo，下次遇到类似问题时通过搜索快速"
                "获取上下文，避免重复犯错。"
            )

        return "根据 Mnemo 公共说明书，我来回答这个问题..."
