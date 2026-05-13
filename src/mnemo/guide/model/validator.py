"""Output validator for Mnemo Guide model responses.

Ensures model-generated output stays on-topic and doesn't make false
claims about its capabilities (internet access, shell access, etc.).
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Banned patterns — any match means the output is rejected
# ---------------------------------------------------------------------------

BANNED_PATTERNS: list[tuple[str, str]] = [
    # (regex pattern, reason description)
    (r"联网|online\b|internet\b|浏览网页|访问网站", "声称有联网/网页访问能力"),
    (r"执行命令|运行命令|shell\b|终端操作|读取文件|读取你.*文件", "声称有 shell/文件访问能力"),
    (r"私人记忆|私人数据|你的记忆|你.*记忆.*内容", "声称能读取私人记忆"),
    (r"上传|上传到服务器|发送到云端|上传.*数据", "声称会上传数据"),
    (r"我给你.*打开|帮你.*打开|我去.*搜索|我可以.*搜索.*网络", "声称能主动打开页面或搜索网络"),
]

# Maximum allowed response length (characters)
MAX_RESPONSE_LENGTH = 2000

# Minimum allowed response length (characters)
MIN_RESPONSE_LENGTH = 10

# Code patterns to check for (the guide should not generate code that's not
# from the knowledge cards)
BANNED_CODE_PATTERNS = [
    r"\bdef\s+\w+\s*\([^)]*\)\s*:",  # Python function definitions
    r"\bclass\s+\w+\s*[:\(]",  # Python class definitions
    r"\bimport\s+\w+",  # Python imports (non-trivial)
    r"\brequire\s*\(|module\.exports",  # Node.js code
]

# Repetition check: if the same sentence appears more than N times, reject
MAX_SENTENCE_REPETITION = 3


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class OutputValidator:
    """Validate model-generated output for safety and relevance.

    Checks that the output:
    1. Is not empty or too short.
    2. Mentions "Mnemo" or related terms.
    3. Does not claim internet/shell/file access.
    4. Does not contain unauthorized code.
    5. Is under the maximum length.
    6. Is not excessively repetitive.
    """

    def validate(
        self, output: str, question: str, intent: str
    ) -> tuple[bool, str | None]:
        """Validate a model output.

        Returns:
            A tuple ``(is_valid, rejection_reason)``. When *is_valid* is
            ``True``, *rejection_reason* is ``None``.
        """
        if not output or not output.strip():
            return False, "输出为空"

        stripped = output.strip()

        # Rule 1: Minimum length
        if len(stripped) < MIN_RESPONSE_LENGTH:
            return False, f"输出过短（{len(stripped)} 字符）"

        # Rule 2: No banned capability claims (check before Mnemo terms
        # so that short-but-dangerous outputs are caught immediately)
        for pattern, reason in BANNED_PATTERNS:
            if re.search(pattern, stripped):
                return False, f"违规声明: {reason}"

        # Rule 3: Must mention Mnemo or related terms
        mnemo_terms = [
            "mnemo",
            "mnemo ",
            "记忆层",
            "agent.*记忆",
            "知识库",
            "知识管理",
        ]
        has_mnemo = False
        for term in mnemo_terms:
            if re.search(term, stripped.lower()):
                has_mnemo = True
                break
        if not has_mnemo:
            return False, "输出未提及 Mnemo 或相关术语"

        # Rule 4: No unauthorized code generation
        for code_pattern in BANNED_CODE_PATTERNS:
            if re.search(code_pattern, stripped):
                return False, "包含未经授权的代码生成"

        # Rule 5: Length limit
        if len(stripped) > MAX_RESPONSE_LENGTH:
            return False, f"输出过长（{len(stripped)}/{MAX_RESPONSE_LENGTH} 字符）"

        # Rule 6: Repetition check
        # Split into sentences (Chinese or English)
        sentences = re.split(r"[。！？.!?\n]+", stripped)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
        seen: dict[str, int] = {}
        for s in sentences:
            key = s[:80]  # First 80 chars as fingerprint
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > MAX_SENTENCE_REPETITION:
                return False, f"检测到重复内容: \"{key[:50]}...\""

        return True, None
