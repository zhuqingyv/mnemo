"""Rule-based intent detector for Mnemo Guide.

Classifies user questions into one of ~13 ``GuideIntent`` values using
keyword matching (Chinese + English). Also detects client names, platform
names, and whether the question should get a hardcoded fixed reply.
"""

from __future__ import annotations

from typing import Optional

from mnemo.guide.fixed_replies import (
    IDENTITY_REPLY,
    OFF_TOPIC_REPLY,
    is_fixed_reply_question,
)
from mnemo.guide.types import GuideIntent, RouterResult


# ---------------------------------------------------------------------------
# Known client name patterns (lowercase)
# ---------------------------------------------------------------------------

CLIENT_PATTERNS: dict[str, str] = {
    "claude code": "claude_code",
    "claude": "claude_code",
    "cursor": "cursor",
    "codebuddy": "codebuddy",
    "codex": "codex",
    "codex cli": "codex",
    "deepseek": "deepseek",
    "kimi": "kimi",
    "windsurf": "windsurf",
    "gemini": "gemini",
    "copilot": "copilot",
    "qwen": "qwen",
}

# ---------------------------------------------------------------------------
# Known platform patterns (lowercase)
# ---------------------------------------------------------------------------

PLATFORM_PATTERNS: dict[str, str] = {
    "mac": "macos",
    "macos": "macos",
    "windows": "windows",
    "win": "windows",
    "linux": "linux",
    "ubuntu": "linux",
    "debian": "linux",
}


# ---------------------------------------------------------------------------
# Intent keyword rules: (GuideIntent, [keyword / phrase, ...])
# Applied in order — first match wins.
# ---------------------------------------------------------------------------

INTENT_RULES: list[tuple[GuideIntent, list[str]]] = [
    (
        "off_topic",
        [
            "http://",
            "https://",
            "网页",
            "打开",
            "天气",
            "游戏",
            "react",
            "javascript",
            "写代码",
            "write code",
        ],
    ),
    (
        "privacy_security",
        [
            "隐私",
            "privacy",
            "安全",
            "security",
            "数据",
            "上传",
            "upload",
        ],
    ),
    (
        "command_template",
        [
            "命令",
            "command",
            "template",
            "模板",
        ],
    ),
    (
        "global_prompt_explain",
        [
            "提示词",
            "prompt",
            "global prompt",
            "全局提示",
        ],
    ),
    (
        "mcp_explain",
        [
            "mcp",
            "model context protocol",
        ],
    ),
    (
        "verify",
        [
            "验证",
            "verify",
            "生效",
            "检查",
            "check",
            "确认",
            "确认生效",
        ],
    ),
    (
        "troubleshooting",
        [
            "报错",
            "失败",
            "fail",
            "error",
            "不行",
            "没有记忆",
            "没出现",
            "没生效",
            "排查",
            "怎么办",
            "troubleshoot",
        ],
    ),
    (
        "install",
        [
            "安装",
            "install",
            "下载",
            "download",
        ],
    ),
    (
        "client_setup",
        [
            "接入",
            "setup",
            "配置",
            "config",
            "注册",
            "register",
        ],
    ),
    (
        "mnemo_overview",
        [
            "mnemo",
            "是什么",
            "what is",
            "介绍",
            "概述",
            "overview",
        ],
    ),
]


def _detect_client(text: str) -> Optional[str]:
    """Return the canonical client name found in *text*, or None."""
    lower = text.lower()
    for pattern, canonical in CLIENT_PATTERNS.items():
        if pattern in lower:
            return canonical
    return None


def _detect_platform(text: str) -> Optional[str]:
    """Return the canonical platform name found in *text*, or None."""
    lower = text.lower()
    for pattern, canonical in PLATFORM_PATTERNS.items():
        if pattern in lower:
            return canonical
    return None


def _detect_intent(text: str) -> GuideIntent:
    """Classify *text* into a ``GuideIntent`` using keyword rules."""
    lower = text.lower()

    for intent, keywords in INTENT_RULES:
        for kw in keywords:
            if kw.lower() in lower:
                return intent

    return "unknown"


class IntentRouter:
    """Rule-based intent detection for Mnemo Guide questions.

    Usage::

        router = IntentRouter()
        result = router.route("如何安装 Mnemo")
        print(result.intent)  # "install"
    """

    def route(self, question: str) -> RouterResult:
        """Detect intent from a user question.

        Steps (in order):
        1. Check fixed-reply patterns (identity, URL, off-topic).
        2. Detect client name.
        3. Detect platform name.
        4. Classify intent via keyword rules.
        """
        # 1. Fixed reply?
        fixed = is_fixed_reply_question(question)
        if fixed is not None:
            # Determine which fixed reply this is to set the right intent
            if fixed == IDENTITY_REPLY:
                return RouterResult(
                    intent="identity",
                    is_fixed_reply=True,
                    fixed_reply_text=fixed,
                )
            elif fixed == OFF_TOPIC_REPLY:
                return RouterResult(
                    intent="off_topic",
                    is_fixed_reply=True,
                    fixed_reply_text=fixed,
                )
            else:
                # CANNOT_OPEN_URL_REPLY
                return RouterResult(
                    intent="off_topic",
                    is_fixed_reply=True,
                    fixed_reply_text=fixed,
                )

        # 2. Detect client
        client = _detect_client(question)

        # 3. Detect platform
        platform = _detect_platform(question)

        # 4. Classify intent
        intent = _detect_intent(question)

        # If a client was detected but the intent is not client_setup,
        # upgrade to client_setup since the user mentioned a specific client.
        if client is not None and intent not in (
            "client_setup",
            "off_topic",
            "identity",
        ):
            if intent in ("install", "unknown"):
                intent = "client_setup"

        return RouterResult(intent=intent, client=client, platform=platform)
