"""Unit tests for classify() in mnemo.relation_types."""

from __future__ import annotations

import pytest

from mnemo.relation_types import (
    ALTERNATIVE_TO,
    CONTRADICTS,
    DEPENDS_ON,
    DERIVED_FROM,
    EXAMPLE_OF,
    LEGACY_RELATION_TYPES,
    REFINES,
    SUPERSEDES,
    VALID_RELATION_TYPES,
    ClassifyInput,
    classify,
    is_valid,
)


def _mk(
    *,
    title: str = "src",
    summary: str = "",
    content: str = "",
    claim_type: str | None = "fact",
    tags: tuple[str, ...] = (),
) -> ClassifyInput:
    return ClassifyInput(
        title=title,
        summary=summary,
        content=content,
        claim_type=claim_type,
        tags=tags,
    )


class TestEnumSet:
    def test_seven_types_exactly(self) -> None:
        assert VALID_RELATION_TYPES == {
            SUPERSEDES,
            CONTRADICTS,
            DEPENDS_ON,
            REFINES,
            DERIVED_FROM,
            ALTERNATIVE_TO,
            EXAMPLE_OF,
        }

    def test_legacy_not_in_valid(self) -> None:
        for legacy in LEGACY_RELATION_TYPES:
            assert legacy not in VALID_RELATION_TYPES

    def test_is_valid(self) -> None:
        assert is_valid("supersedes") is True
        assert is_valid("wikilink") is False
        assert is_valid("") is False


class TestRule0Supersedes:
    def test_supersedes_preserved(self) -> None:
        src = _mk(content="arbitrary text matters not")
        tgt = _mk(title="t")
        assert classify(src=src, tgt=tgt, current_type="supersedes") == SUPERSEDES


class TestRule1Tags:
    def test_fact_correction_tag_requires_tgt_in_correction_context(self) -> None:
        # tag alone is NOT enough — tgt must appear near a correction marker.
        tgt = _mk(title="旧事实 A")
        src_pos = _mk(
            content="事实更正：之前报告 旧事实 A 不准，现在更新。",
            tags=("fact-correction",),
        )
        assert classify(src=src_pos, tgt=tgt, current_type="wikilink") == CONTRADICTS

        src_neg = _mk(
            content="事实更正：某无关事实被更正",
            tags=("fact-correction",),
        )
        assert classify(src=src_neg, tgt=tgt, current_type="wikilink") != CONTRADICTS

    def test_rejected_option_tag_requires_tgt_in_rejection_context(self) -> None:
        tgt = _mk(title="方案 A")
        src_pos = _mk(
            content="放弃方案：方案 A 因成本过高被否决。",
            tags=("rejected-option",),
            claim_type="decision",
        )
        assert (
            classify(src=src_pos, tgt=tgt, current_type="related")
            == ALTERNATIVE_TO
        )

        tgt2 = _mk(title="产品定位")
        src_neg = _mk(
            content="放弃方案：另一个无关选项被弃",
            tags=("rejected-option",),
            claim_type="decision",
        )
        assert (
            classify(src=src_neg, tgt=tgt2, current_type="related")
            != ALTERNATIVE_TO
        )


class TestRule2Keywords:
    @pytest.mark.parametrize(
        "snippet,tgt_title,expected",
        [
            ("X 依赖 模块 Y", "模块 Y", DEPENDS_ON),
            ("前置条件: 安装好 ollama", "ollama", DEPENDS_ON),
            ("必须先 跑迁移脚本", "跑迁移脚本", DEPENDS_ON),
            # refines is the one weak type that fires on keyword alone.
            ("细化了存储格式", "unrelated target", REFINES),
            ("详见 architecture.md", "unrelated target", REFINES),
            ("进一步展开讨论", "unrelated target", REFINES),
            ("来源于 session e8c1e554", "session e8c1e554", DERIVED_FROM),
            ("参考 basic-memory 架构", "basic-memory 架构", DERIVED_FROM),
            ("借鉴了 mem0 的思路", "mem0 的思路", DERIVED_FROM),
            ("例如 INT-17 case", "INT-17 case", EXAMPLE_OF),
            ("比如 mnemo MCP", "mnemo MCP", EXAMPLE_OF),
            ("以 Ollama 为例", "Ollama", EXAMPLE_OF),
        ],
    )
    def test_safe_keywords(
        self, snippet: str, tgt_title: str, expected: str
    ) -> None:
        src = _mk(content=snippet)
        tgt = _mk(title=tgt_title)
        assert classify(src=src, tgt=tgt, current_type="wikilink") == expected

    def test_strong_types_require_tgt_in_span(self) -> None:
        """Bare keyword without tgt in span must NOT fire the strong type."""
        tgt = _mk(title="unrelated target")
        # "依赖" present but tgt title not in span — falls back to refines.
        src = _mk(content="X 依赖 Y 才能启动")
        assert classify(src=src, tgt=tgt, current_type="wikilink") != DEPENDS_ON

        src2 = _mk(content="参考 basic-memory 架构")
        assert classify(src=src2, tgt=tgt, current_type="wikilink") != DERIVED_FROM

        src3 = _mk(content="例如 INT-17 case")
        assert classify(src=src3, tgt=tgt, current_type="wikilink") != EXAMPLE_OF

    def test_contradict_requires_tgt_near_correction_marker(self) -> None:
        tgt = _mk(title="旧事实 X")
        src_pos = _mk(
            content="事实更正：旧事实 X 之前描述有误，实际应为 Y。"
        )
        assert classify(src=src_pos, tgt=tgt, current_type="wikilink") == CONTRADICTS

        src_neg = _mk(
            content="本决策支持矛盾检测能力，但与当前 tgt 无关"
        )
        assert classify(src=src_neg, tgt=tgt, current_type="wikilink") != CONTRADICTS

    def test_port_conflict_does_not_fire_contradicts(self) -> None:
        # Previous rule fired contradicts on "端口冲突"; must NOT happen now.
        tgt = _mk(title="E2E 流程")
        src = _mk(content="跑测试前清残留进程避免端口冲突。")
        assert classify(src=src, tgt=tgt, current_type="wikilink") != CONTRADICTS

    def test_user_correcting_agent_not_contradicts(self) -> None:
        # "用户纠正过的规则" is not a knowledge-level contradiction.
        # Bare "纠正" must not fire CONTRADICTS.
        tgt = _mk(title="发现新规则主动问是否持久化", claim_type="decision")
        src = _mk(
            content=(
                "典型吐槽：之前好几个 Agent 修复问题删了好几次。"
                "用户纠正过的规则要持久化生效（关联 [[发现新规则主动问是否持久化]]）。"
            ),
            claim_type="decision",
        )
        assert classify(src=src, tgt=tgt, current_type="wikilink") != CONTRADICTS

    def test_alternative_requires_same_claim_type_and_rejection_marker(
        self,
    ) -> None:
        tgt = _mk(title="方案 A", claim_type="decision")
        src_pos = _mk(
            content="放弃方案：方案 A 成本太高。",
            claim_type="decision",
        )
        assert (
            classify(src=src_pos, tgt=tgt, current_type="wikilink")
            == ALTERNATIVE_TO
        )

        src_diff_ct = _mk(
            content="放弃方案：方案 A 成本太高。",
            claim_type="fact",
        )
        assert (
            classify(src=src_diff_ct, tgt=tgt, current_type="wikilink")
            != ALTERNATIVE_TO
        )

    def test_precedence_depends_beats_refines(self) -> None:
        tgt = _mk(title="X")
        src = _mk(content="必须先完成 X，详见 doc.md")
        assert classify(src=src, tgt=tgt, current_type="related") == DEPENDS_ON

    def test_summary_also_scanned(self) -> None:
        tgt = _mk(title="模块 Y")
        src = _mk(summary="X 依赖 模块 Y", content="无关内容")
        assert classify(src=src, tgt=tgt, current_type="related") == DEPENDS_ON


class TestRule3ClaimTypeFallback:
    """New, conservative matrix. Same-type pairs default to refines."""

    @pytest.mark.parametrize(
        "src_ct,tgt_ct,expected",
        [
            ("decision", "fact", DERIVED_FROM),
            ("decision", "decision", REFINES),
            ("procedure", "procedure", REFINES),
            ("procedure", "fact", DERIVED_FROM),
            ("fact", "fact", REFINES),
            ("fact", "decision", DERIVED_FROM),
            ("hypothesis", "fact", DERIVED_FROM),
            ("hypothesis", "decision", DERIVED_FROM),
            ("procedure", "decision", REFINES),
            ("decision", "procedure", REFINES),
        ],
    )
    def test_matrix(self, src_ct: str, tgt_ct: str, expected: str) -> None:
        src = _mk(
            content="纯陈述无关键词", claim_type=src_ct, title="src title"
        )
        tgt = _mk(claim_type=tgt_ct, title="tgt title")
        assert classify(src=src, tgt=tgt, current_type="wikilink") == expected


class TestRule4FinalFallback:
    def test_unknown_claim_types_fall_to_refines(self) -> None:
        src = _mk(content="无语义线索", claim_type=None)
        tgt = _mk(claim_type=None)
        assert classify(src=src, tgt=tgt, current_type="wikilink") == REFINES


class TestOutputDomain:
    def test_output_always_in_valid_set(self) -> None:
        for ct in ["fact", "decision", "procedure", "hypothesis", None, "weird"]:
            for snippet in ["", "例如", "依赖", "矛盾", "无线索", "备选方案"]:
                src = _mk(content=snippet, claim_type=ct)
                for tgt_ct in ["fact", "decision", "procedure", None]:
                    tgt = _mk(claim_type=tgt_ct, title="tgt")
                    out = classify(src=src, tgt=tgt, current_type="wikilink")
                    assert out in VALID_RELATION_TYPES


class TestTgtTitleWindow:
    """Edge cases around the _tgt_near_marker helper."""

    def test_tgt_immediately_after_marker(self) -> None:
        tgt = _mk(title="方案 A", claim_type="decision")
        src = _mk(
            content="放弃方案：方案 A",
            claim_type="decision",
        )
        assert (
            classify(src=src, tgt=tgt, current_type="wikilink")
            == ALTERNATIVE_TO
        )

    def test_tgt_far_from_marker_no_fire(self) -> None:
        tgt = _mk(title="方案 A", claim_type="decision")
        long_filler = "x" * 200
        src = _mk(
            content=f"放弃方案：别的东西。{long_filler}方案 A 在这里毫无关系出现",
            claim_type="decision",
        )
        out = classify(src=src, tgt=tgt, current_type="wikilink")
        assert out != ALTERNATIVE_TO
