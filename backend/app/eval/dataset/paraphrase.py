"""Paraphrase robustness generator (NL2SQL其他测评方法-PRD.md FR-METHOD-09).

Same intent, N different phrasings — measure output stability.

Two ways to generate variants:
  1. LLM-driven (high quality, costs tokens): ParaphraseGenerator
  2. Rule-driven (deterministic, free): RuleParaphraser

Best practice: use rule-driven for CI (fast, repeatable), LLM-driven
for offline analysis (catches semantic paraphrases rule-based misses).

Stability metric:
  paraphrase_stability = avg(ast_sim(SQL_i, SQL_j)) for all i,j pairs

High stability = Agent is robust to wording; low = brittle (fix prompt).
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from typing import Iterable

from ..utils import ast_similarity

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Rule-based paraphraser
# --------------------------------------------------------------------------- #
# Domain-agnostic Chinese synonym swaps. Conservative — only applies
# when the source token is unambiguous. Extend per-domain as needed.
_SYNONYM_GROUPS: list[list[str]] = [
    ["多少", "几", "啥数"],
    ["销售", "成交", "卖出"],
    ["金额", "额度", "总额", "价钱"],
    ["今天", "当日", "本日"],
    ["昨天", "昨日"],
    ["本周", "这周"],
    ["上周", "上一周"],
    ["本月", "这个月", "当月"],
    ["上月", "上个月", "_previous月"],
    ["查询", "查一下", "帮我看", "看一下"],
    ["统计", "汇总", "合计"],
    ["占比", "比例", "百分比"],
    ["排名", "排行", "TOP"],
    ["对比", "比较", "vs"],
    ["趋势", "走势", "变化"],
    ["客户", "用户", "顾客"],
    ["商品", "SKU", "货品"],
    ["订单", "单子"],
    ["地区", "区域", "地带"],
    ["数据", "记录"],
]
_SYNONYM_MAP: dict[str, list[str]] = {}
for _grp in _SYNONYM_GROUPS:
    for _w in _grp:
        _SYNONYM_MAP[_w] = [x for x in _grp if x != _w]


# Generic question-form transforms (apply to any Chinese NL question).
_QUESTION_FORMS = [
    # (pattern_matcher, transformer)
    # "X 是多少" → "X 是多少呢" / "请问 X" / "X 总共多少"
    (re.compile(r"^(.+?)是多少\??$"),
     lambda m: [
         f"请问{m.group(1)}是多少",
         f"{m.group(1)}总共有多少",
         f"{m.group(1)}的数据是多少呢",
     ]),
    # "查 X" → "帮我查 X" / "X 是多少"
    (re.compile(r"^查(?:询|一下)?\s*(.+?)[?？]?$"),
     lambda m: [
         f"帮我查{m.group(1)}",
         f"看一下{m.group(1)}",
         f"{m.group(1)}是多少",
     ]),
]


@dataclass
class RuleParaphraser:
    """Deterministic Chinese NL paraphraser.

    Cheap (no LLM), repeatable, and CI-safe. Quality is lower than
    LLM paraphrases but enough to catch prompt over-fitting.
    """
    rng: random.Random = field(default_factory=lambda: random.Random(42))

    def paraphrase(self, question: str, n: int = 3) -> list[str]:
        """Generate up to `n` paraphrases. May return fewer if the
        question is too short or doesn't match any rule."""
        if not question or n <= 0:
            return []
        out: list[str] = []
        seen = {question}

        # 1. Question-form transforms.
        for pat, fn in _QUESTION_FORMS:
            m = pat.match(question.strip())
            if m:
                for cand in fn(m):
                    if cand not in seen:
                        out.append(cand)
                        seen.add(cand)
                    if len(out) >= n:
                        return out

        # 2. Synonym substitution.
        words = list(_SYNONYM_MAP.keys())
        self.rng.shuffle(words)
        for w in words:
            if len(out) >= n:
                break
            if w in question:
                for alt in _SYNONYM_MAP[w]:
                    cand = question.replace(w, alt, 1)
                    if cand not in seen:
                        out.append(cand)
                        seen.add(cand)
                        break

        # 3. Polite-prefix/suffix wrap (cheap fallback).
        prefixes = ["请问", "麻烦问下", "帮我看看", "我想知道"]
        suffixes = ["呢", "啊", "?", "？"]
        for p in prefixes:
            if len(out) >= n:
                break
            cand = f"{p}{question.strip().rstrip('?？')}"
            if cand not in seen:
                out.append(cand)
                seen.add(cand)
        for s in suffixes:
            if len(out) >= n:
                break
            cand = f"{question.rstrip()} {s}".strip()
            if cand not in seen:
                out.append(cand)
                seen.add(cand)

        return out[:n]


# --------------------------------------------------------------------------- #
# LLM-driven paraphraser
# --------------------------------------------------------------------------- #
_PARAPHRASE_PROMPT = """你需要把用户的问数问题改写成 {n} 个语义完全相同、但措辞不同的版本。

要求：
- 语义必须与原问题严格等价（同一个指标、同一时间范围、同一筛选条件）
- 措辞尽量多样化：口语/书面、长句/短句、不同动词
- 不要改变数字、时间、商品名等实体
- 每行一个变体，不要编号、不要引号

原问题：{question}

输出（{n} 个变体，每行一个）："""


@dataclass
class LLMParaphraser:
    """LLM-driven paraphraser. Use any callable LLM client that supports
    `async invoke(prompt: str) -> str`.

    Quality is much higher than rule-based; cost is non-zero. Best used
    for offline dataset generation, NOT in CI.
    """
    llm_client: object = None  # any object with async invoke()
    n: int = 5

    async def paraphrase(self, question: str) -> list[str]:
        if not self.llm_client:
            raise RuntimeError("llm_client required for LLMParaphraser")
        prompt = _PARAPHRASE_PROMPT.format(n=self.n, question=question)
        try:
            raw = await self.llm_client.invoke(prompt)
        except Exception as e:
            logger.warning(f"LLM paraphrase failed: {e}")
            return []
        # Parse non-empty stripped lines.
        lines = [ln.strip().lstrip("0123456789.-、）) ").strip()
                 for ln in raw.splitlines()]
        lines = [ln for ln in lines if ln and ln != question]
        return lines[: self.n]


# --------------------------------------------------------------------------- #
# Stability scoring
# --------------------------------------------------------------------------- #
def pairwise_ast_similarity(sqls: list[str]) -> float:
    """Mean pairwise AST similarity across N SQLs.

    Returns 1.0 for ≤1 SQL (trivially stable), 0.0-1.0 otherwise.
    Use this as the stability metric per PRD FR-METHOD-09.
    """
    n = len(sqls)
    if n <= 1:
        return 1.0
    sims = []
    for i in range(n):
        for j in range(i + 1, n):
            sims.append(ast_similarity(sqls[i], sqls[j]))
    return sum(sims) / len(sims) if sims else 0.0


@dataclass
class ParaphraseReport:
    """Per-question stability report."""
    question: str
    variants: list[str] = field(default_factory=list)
    pred_sqls: list[str] = field(default_factory=list)
    stability: float = 0.0
    all_passed_threshold: bool = False

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "variants": self.variants,
            "pred_sqls": self.pred_sqls,
            "stability": round(self.stability, 4),
            "passed": self.all_passed_threshold,
        }


def evaluate_paraphrase_stability(
    question: str,
    variants: list[str],
    pred_sqls_per_variant: list[str],
    *,
    stability_threshold: float = 0.85,
) -> ParaphraseReport:
    """Compute stability for one original question + N variants.

    Args:
        question: the original NL question (for traceability).
        variants: list of paraphrased NL questions (length N).
        pred_sqls_per_variant: SQL produced for EACH variant (length N).
            Note: this should include SQLs only for variants, not the
            original — the original isn't compared against itself.
        stability_threshold: minimum acceptable pairwise AST similarity.

    Returns:
        ParaphraseReport with stability score and pass/fail flag.
    """
    if len(variants) != len(pred_sqls_per_variant):
        raise ValueError(
            f"variants ({len(variants)}) must match pred_sqls "
            f"({len(pred_sqls_per_variant)})"
        )
    stability = pairwise_ast_similarity(pred_sqls_per_variant)
    return ParaphraseReport(
        question=question,
        variants=variants,
        pred_sqls=pred_sqls_per_variant,
        stability=stability,
        all_passed_threshold=(stability >= stability_threshold),
    )


# --------------------------------------------------------------------------- #
# Aggregation across a run
# --------------------------------------------------------------------------- #
@dataclass
class ParaphraseSummary:
    """Per-run aggregate of paraphrase stability."""
    total: int = 0
    passed: int = 0
    avg_stability: float = 0.0
    worst: list[dict] = field(default_factory=list)  # bottom-5 by stability

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "pass_rate": round(self.passed / self.total, 4) if self.total else 0.0,
            "avg_stability": round(self.avg_stability, 4),
            "worst": self.worst,
        }


def summarize_paraphrase(
    reports: list[ParaphraseReport],
    bottom_n: int = 5,
) -> ParaphraseSummary:
    """Aggregate per-question paraphrase reports.

    The `worst` list shows the bottom-N questions by stability — these
    are the ones whose SQL changes most across rephrasings (most
    over-fitted to specific wording).
    """
    s = ParaphraseSummary(total=len(reports))
    if not reports:
        return s
    s.passed = sum(1 for r in reports if r.all_passed_threshold)
    s.avg_stability = sum(r.stability for r in reports) / len(reports)
    sorted_by_stability = sorted(reports, key=lambda r: r.stability)
    s.worst = [
        {"question": r.question, "stability": round(r.stability, 4)}
        for r in sorted_by_stability[:bottom_n]
    ]
    return s
