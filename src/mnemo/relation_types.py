"""Typed relation vocabulary for mnemo's knowledge graph.

Phase 2 M3a expands the relation.relation_type value domain from three
legacy types (wikilink / related / supersedes) to seven semantic types.
The column itself is untouched — this module is the authoritative whitelist
plus a pure ``classify`` function used by the M3a backfill script and, later,
by L3 graph reranking.

Rule precedence:
  Rule 0: ``supersedes`` preserved verbatim (version chains are authoritative).
  Rule 1: tag + context cross-check (fact-correction / rejected-option,
          but only when tgt.title appears inside the correction / rejection
          window of src.content — avoids firing on unrelated edges).
  Rule 2: keyword + span-anchor match. ALL strong types (contradicts /
          depends_on / derived_from / example_of / alternative_to) require
          the tgt title to sit inside the marker-opened span. Bare keyword
          match is not enough — a phrase like "依赖 X" in src.content tells
          you src has *some* dependency, but not that it depends on tgt
          specifically. refines is the one weak type that fires on keyword
          alone (neutral "related content" label).
  Rule 3: claim_type cross-product fallback — conservative: same-type
          decisions / procedures land on ``refines`` rather than guessing
          ``alternative_to`` / ``depends_on``.
  Rule 4: final fallback to ``refines`` so no edge is left dangling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SUPERSEDES = "supersedes"
CONTRADICTS = "contradicts"
DEPENDS_ON = "depends_on"
REFINES = "refines"
DERIVED_FROM = "derived_from"
ALTERNATIVE_TO = "alternative_to"
EXAMPLE_OF = "example_of"

VALID_RELATION_TYPES: frozenset[str] = frozenset(
    {
        SUPERSEDES,
        CONTRADICTS,
        DEPENDS_ON,
        REFINES,
        DERIVED_FROM,
        ALTERNATIVE_TO,
        EXAMPLE_OF,
    }
)

LEGACY_RELATION_TYPES: frozenset[str] = frozenset({"wikilink", "related"})


# Strong-signal phrases that indicate src is correcting / contradicting
# something. Kept very tight — bare "冲突"/"纠正"/"误判" are too noisy
# ("端口冲突"/"Agent 被纠正"/"误判 bug") so we require explicit "事实/立场/决策"
# prefixes or specific "不是 X 而是 Y" framing.
_CONTRADICT_PATTERN = re.compile(
    r"事实更正|推翻之前|反对.{0,6}方案"
    r"|事实矛盾|立场矛盾|决策矛盾|与.{0,12}矛盾|不是.{0,6}而是"
)

_DEPENDS_PATTERN = re.compile(r"依赖|前置|必须先|需要先|前提")
_REFINES_PATTERN = re.compile(r"细化|详见|进一步|展开|阐述|具体说")
_DERIVED_PATTERN = re.compile(r"来源于|源自|出自|参考|借鉴|衍生|提取自")
_EXAMPLE_PATTERN = re.compile(r"例如|比如|示例|例子|以.{0,10}为例")

# Marker phrases that open a "rejected alternative" paragraph in src.content.
# A tgt title appearing within a small window of one of these is strong
# evidence the edge is src --alternative_to--> tgt.
_REJECTION_MARKERS: tuple[str, ...] = (
    "放弃方案",
    "放弃的方案",
    "替代方案",
    "备选方案",
    "已否决",
    "被否决",
    "不采纳",
    "最终未采纳",
    "rejected",
)
_REJECTION_WINDOW = 80  # chars between marker and tgt title

# Marker phrases that open a "correction / refutation" paragraph in
# src.content. "纠正"/"误判" are NOT included here — they appear in benign
# contexts like "用户纠正过的规则" / "误判 bug 类型" where no knowledge-level
# contradiction exists. Only explicit factual-correction markers are kept.
_CORRECTION_MARKERS: tuple[str, ...] = (
    "事实更正",
    "推翻",
    "之前的说法",
    "之前误",
    "并非",
    "不是.*而是",
)
_CORRECTION_WINDOW = 80

# Marker phrases for depends_on. Must sit next to tgt title — bare "依赖"
# in "该决策依赖模块 Y" doesn't prove the edge src → tgt is a dependency
# unless tgt is named in the same span.
_DEPENDS_MARKERS: tuple[str, ...] = (
    "依赖",
    "前置",
    "必须先",
    "需要先",
    "前提",
)
_DEPENDS_WINDOW = 40

# Marker phrases for derived_from. Same span-anchor requirement — "参考"/
# "借鉴" without tgt in the span would fire on any mention of borrowing.
_DERIVED_MARKERS: tuple[str, ...] = (
    "来源于",
    "源自",
    "出自",
    "参考",
    "借鉴",
    "衍生",
    "提取自",
)
_DERIVED_WINDOW = 40

# Marker phrases for example_of. Must sit next to tgt title — "例如 X"
# where X != tgt should not fire.
_EXAMPLE_MARKERS: tuple[str, ...] = (
    "例如",
    "比如",
    "示例",
    "例子",
    "以.{0,10}为例",
)
_EXAMPLE_WINDOW = 30

_CONTRADICT_TAGS: frozenset[str] = frozenset({"fact-correction"})
_ALTERNATIVE_TAGS: frozenset[str] = frozenset({"rejected-option"})


@dataclass(frozen=True)
class ClassifyInput:
    """Minimal view a ``Knowledge`` row needs to be classified.

    Using a dataclass instead of the ORM class keeps ``classify`` pure and
    easy to test without a DB session.
    """

    title: str
    summary: str
    content: str
    claim_type: str | None
    tags: tuple[str, ...] = ()


def _combined_text(k: ClassifyInput) -> str:
    return f"{k.summary}\n{k.content}"


_SPAN_TERMINATORS = re.compile(r"[。；\n]|关联[:：]")

# Matches the tail-end "关联：[[tgt]]、[[tgt2]]" reference block common in
# mnemo fixtures. tgt titles listed here are "related in some way" to src —
# the body prose explains HOW (via 依赖/参考/例如/…).
_LINK_BLOCK_RE = re.compile(r"关联[:：]([^\n]*)")


def _tgt_in_link_block(src_text: str, tgt_title: str) -> bool:
    if not tgt_title:
        return False
    for m in _LINK_BLOCK_RE.finditer(src_text):
        if tgt_title in m.group(1):
            return True
    return False


def _tgt_in_marker_span(
    src_text: str, tgt_title: str, markers: tuple[str, ...], window: int
) -> bool:
    """True when tgt_title appears inside the span that a marker opens.

    Previous implementation used a symmetric proximity window which
    mis-fired on paragraphs of the shape
    ``放弃方案：X、Y、Z。关联：[[tgt_title]]`` — tgt sat within 80 chars
    of the marker even though tgt was listed under *关联* (related
    references), not under *放弃方案* (rejected options).

    New rule: the marker opens a span that ends at the next sentence
    terminator (``。``/``；``/newline) or the start of a ``关联：`` block,
    whichever comes first. tgt must lie *inside* that span — and also
    within ``window`` chars of the marker to keep long enumerations in
    check. ``不是.*而是`` style markers also match if tgt sits inside the
    matched regex itself.
    """
    if not tgt_title:
        return False
    tgt_idx = src_text.find(tgt_title)
    if tgt_idx == -1:
        return False
    tgt_end = tgt_idx + len(tgt_title)

    for marker in markers:
        for m in re.finditer(marker, src_text):
            m_start, m_end = m.span()

            # Case A: tgt lies inside the regex match itself (e.g.
            # "不是 X 而是 Y" captured a span that includes tgt).
            if m_start <= tgt_idx and tgt_end <= m_end:
                return True

            # Case B: tgt follows the marker. Walk forward from m_end until
            # we hit a span terminator; tgt must lie before that point.
            if tgt_idx >= m_end:
                terminator = _SPAN_TERMINATORS.search(src_text, m_end)
                span_end = terminator.start() if terminator else len(src_text)
                if tgt_end <= span_end and (tgt_idx - m_end) <= window:
                    return True
    return False


# Keep the older name around for any direct importers in tests that may
# reference the helper by the old spelling.
_tgt_near_marker = _tgt_in_marker_span


def _rule1_tags(src: ClassifyInput, tgt: ClassifyInput) -> str | None:
    """Tag-driven classification — only fires when tgt is directly implicated
    by src's correction / rejection context. A tag alone is not enough."""
    tag_set = {t.lower() for t in src.tags}
    src_text = _combined_text(src)

    if tag_set & _CONTRADICT_TAGS:
        if _tgt_near_marker(
            src_text, tgt.title, _CORRECTION_MARKERS, _CORRECTION_WINDOW
        ):
            return CONTRADICTS

    if tag_set & _ALTERNATIVE_TAGS:
        if _tgt_near_marker(
            src_text, tgt.title, _REJECTION_MARKERS, _REJECTION_WINDOW
        ):
            return ALTERNATIVE_TO

    return None


def _rule2_keywords(src: ClassifyInput, tgt: ClassifyInput) -> str | None:
    """Keyword matching with span-anchor guard rails.

    All strong types (contradicts / depends_on / derived_from / example_of /
    alternative_to) require the tgt title to sit inside the span opened by
    one of the marker phrases — otherwise the keyword is just noise and the
    edge falls back to Rule 3 (claim_type matrix → usually refines).

    refines is the only weak type that fires on bare keyword match, because
    it's the neutral "related content" label and misfiring costs less.
    """
    text = _combined_text(src)

    if _CONTRADICT_PATTERN.search(text) and _tgt_in_marker_span(
        text, tgt.title, _CORRECTION_MARKERS, _CORRECTION_WINDOW
    ):
        return CONTRADICTS

    # If src is a "rejection page" (has 放弃方案/被否决/… markers), the
    # keywords "依赖"/"参考"/"例如" inside the body usually describe the
    # rejected option's traits, not the real edge to tgt. Skip strong types
    # in that case — tgt will fall back to refines via Rule 3.
    has_rejection_frame = any(
        re.search(m, text) for m in _REJECTION_MARKERS
    )

    # depends_on / derived_from / example_of: accept either a tight
    # marker-span anchor OR a "关联：[[tgt]]" reference block anchor paired
    # with the keyword appearing in src body. The 关联 block lists tgts
    # related to src; the body keyword explains *how*.
    if not has_rejection_frame:
        if _DEPENDS_PATTERN.search(text) and (
            _tgt_in_marker_span(text, tgt.title, _DEPENDS_MARKERS, _DEPENDS_WINDOW)
            or _tgt_in_link_block(text, tgt.title)
        ):
            return DEPENDS_ON

    same_claim = (
        bool(src.claim_type)
        and (src.claim_type or "") == (tgt.claim_type or "")
    )
    if same_claim and _tgt_in_marker_span(
        text, tgt.title, _REJECTION_MARKERS, _REJECTION_WINDOW
    ):
        return ALTERNATIVE_TO

    if not has_rejection_frame:
        if _DERIVED_PATTERN.search(text) and (
            _tgt_in_marker_span(text, tgt.title, _DERIVED_MARKERS, _DERIVED_WINDOW)
            or _tgt_in_link_block(text, tgt.title)
        ):
            return DERIVED_FROM

        # example_of is strict — only fires when tgt sits in the tight
        # "例如 X" / "以 X 为例" span. Link-block alone is not enough
        # because src may list example phrases ("例如'好的！'") that are
        # literal strings, not knowledge refs; the 关联 block then refers
        # to something else entirely.
        if _EXAMPLE_PATTERN.search(text) and _tgt_in_marker_span(
            text, tgt.title, _EXAMPLE_MARKERS, _EXAMPLE_WINDOW
        ):
            return EXAMPLE_OF

    if _REFINES_PATTERN.search(text):
        return REFINES

    return None


def _rule3_claim_type_fallback(src: ClassifyInput, tgt: ClassifyInput) -> str:
    """Conservative claim_type matrix.

    Prior matrix over-fired alternative_to / depends_on for same-type pairs.
    New rule: same-type edges fall back to ``refines`` (neutral 'related
    content' label); only cross-type edges where causal direction is
    unambiguous stay on ``derived_from``.
    """
    sct = (src.claim_type or "").lower()
    tct = (tgt.claim_type or "").lower()

    if sct == "decision" and tct == "fact":
        return DERIVED_FROM
    if sct == "procedure" and tct == "fact":
        return DERIVED_FROM
    if sct == "fact" and tct == "decision":
        return DERIVED_FROM
    if sct == "hypothesis":
        return DERIVED_FROM

    return REFINES


def classify(
    *,
    src: ClassifyInput,
    tgt: ClassifyInput,
    current_type: str | None,
) -> str:
    """Return a valid relation type for the edge ``src -> tgt``."""
    if current_type == SUPERSEDES:
        return SUPERSEDES

    tagged = _rule1_tags(src, tgt)
    if tagged is not None:
        return tagged

    matched = _rule2_keywords(src, tgt)
    if matched is not None:
        return matched

    return _rule3_claim_type_fallback(src, tgt)


def is_valid(relation_type: str) -> bool:
    return relation_type in VALID_RELATION_TYPES
