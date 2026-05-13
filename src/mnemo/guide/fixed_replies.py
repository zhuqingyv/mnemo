"""Fixed replies for Mnemo Guide — answers that NEVER call a model.

These are hardcoded responses for identity, capability, off-topic, and
URL-related questions. The guide exposes these deterministically so the
answer is always correct regardless of model availability.
"""

from typing import Optional

IDENTITY_REPLY = (
    "我是 Mnemo Guide，本地说明书助手。\n"
    "我只能回答 Mnemo 的安装、配置、MCP 接入、全局提示词、FAQ 和错误排查问题。\n"
    "我不会读取你的私人记忆、不会执行命令，也不会上传你的问题。"
)

OFF_TOPIC_REPLY = (
    "这个助手只回答 Mnemo 相关问题。\n"
    "你可以问我：\n"
    "- Mnemo 是什么\n"
    "- 怎么安装 Mnemo\n"
    "- 如何接入 Claude Code / Cursor / CodeBuddy / Codex CLI\n"
    "- MCP 注入是什么\n"
    "- 全局提示词注入是什么\n"
    "- Agent 没有记忆怎么办\n"
    "- 如何验证 Mnemo 是否生效"
)

CANNOT_OPEN_URL_REPLY = (
    "我不能打开网页或联网浏览。但我内置了 Mnemo 的公共说明书，"
    "可以回答安装、配置和接入问题。\n"
    "你可以直接问我：怎么安装 Mnemo，或者选择你的客户端："
    "Claude Code / Cursor / CodeBuddy / Codex CLI。"
)


def is_fixed_reply_question(question: str) -> Optional[str]:
    """Check if a question triggers a fixed (non-model) reply.

    Returns the fixed reply text if the question matches a known pattern,
    or None if the question should be routed through the normal chain.

    Match rules are case-insensitive.
    """
    q = question.strip().lower()

    # Identity / capability
    if any(kw in q for kw in ["你是谁", "who are you"]):
        return IDENTITY_REPLY

    if any(kw in q for kw in ["你能做什么", "你有什么能力", "what can you do"]):
        return IDENTITY_REPLY

    if any(
        kw in q
        for kw in [
            "你能打开网页",
            "你能联网",
            "你能浏览",
            "open url",
            "你会调用工具",
            "你能执行命令",
            "你能读取记忆",
            "私人记忆",
            "读取我的",
        ]
    ):
        return IDENTITY_REPLY

    # URL detection
    if "http://" in q or "https://" in q:
        return CANNOT_OPEN_URL_REPLY

    # Off-topic keywords
    off_topic_keywords = [
        "chrome",
        "google",
        "react",
        "游戏",
        "天气",
        "写代码",
        "帮我写",
    ]
    if any(kw.lower() in q for kw in off_topic_keywords):
        return OFF_TOPIC_REPLY

    return None
