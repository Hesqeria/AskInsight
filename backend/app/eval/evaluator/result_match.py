"""结果集匹配工具（L3 EX 评测核心）

支持 4 种匹配模式：
1. 完全相等（含顺序，用于 ORDER BY 查询）
2. 集合相等（无 ORDER BY）
3. 数值容差（FLOAT/DECIMAL 列，1e-6）
4. 形状不同 → 直接 False
"""
from dataclasses import dataclass
from typing import Any
import math


@dataclass
class MatchResult:
    exact: bool = False          # 完全相等
    set_equal: bool = False      # 集合相等（忽略顺序）
    within_tolerance: bool = False  # 容差范围内相等
    final_pass: bool = False     # 综合判定
    reason: str = ""

    def __bool__(self):
        return self.final_pass


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _rows_close(a: list, b: list, rtol: float = 1e-6) -> bool:
    """逐元素比较，数值列用容差"""
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if len(ra) != len(rb):
            return False
        for va, vb in zip(ra, rb):
            if _is_number(va) and _is_number(vb):
                if math.isclose(va, vb, rel_tol=rtol, abs_tol=1e-9):
                    continue
                return False
            elif va != vb:
                return False
    return True


def match(gold: list[list], pred: list[list], rtol: float = 1e-6) -> MatchResult:
    """比较两个结果集

    Args:
        gold: 金标准结果集（list of tuples/lists）
        pred: 候选结果集
        rtol: 数值容差
    """
    res = MatchResult()

    # 空结果处理
    if not gold and not pred:
        res.exact = True
        res.set_equal = True
        res.within_tolerance = True
        res.final_pass = True
        res.reason = "both empty"
        return res

    # 形状检查
    if len(gold) == 0 or len(pred) == 0:
        res.reason = f"one side empty: gold={len(gold)} pred={len(pred)}"
        return res

    if len(gold[0]) != len(pred[0]):
        res.reason = f"列数不同: gold={len(gold[0])} pred={len(pred[0])}"
        return res

    # 1. 完全相等（含顺序）
    try:
        if list(map(tuple, gold)) == list(map(tuple, pred)):
            res.exact = True
            res.set_equal = True
            res.within_tolerance = True
            res.final_pass = True
            res.reason = "完全一致"
            return res
    except Exception:
        pass

    # 2. 容差比较（同顺序）
    if len(gold) == len(pred):
        if _rows_close(gold, pred, rtol):
            res.within_tolerance = True
            res.set_equal = True
            res.final_pass = True
            res.reason = "数值容差内一致"
            return res

    # 3. 集合相等（无序）
    try:
        gold_set = set(map(tuple, gold))
        pred_set = set(map(tuple, pred))
        if gold_set == pred_set:
            res.set_equal = True
            res.final_pass = True
            res.reason = "集合一致（无序）"
            return res
    except Exception:
        pass

    # 3.5 列顺序归一化（列顺序不同但语义等价）
    #    例: gold=(dt, gmv), pred=(SUM(gmv), dt) — 列位置不同但内容一致
    #    处理: 对每行按值排序（元素级归一化），再做无序集合比较
    if len(gold) == len(pred) and len(gold[0]) == len(pred[0]):
        try:
            def _sortable(v):
                # Decimal/date/datetime → 可比较的 key（字符串 + 类型名避免异型误判）
                return (type(v).__name__, str(v))
            gold_norm = {tuple(sorted((_sortable(c) for c in r), key=lambda x: x)) for r in gold}
            pred_norm = {tuple(sorted((_sortable(c) for c in r), key=lambda x: x)) for r in pred}
            if gold_norm == pred_norm:
                res.set_equal = True
                res.final_pass = True
                res.reason = "列顺序不同但内容一致（列序归一化）"
                return res
        except Exception:
            pass

    # 4. 集合 + 容差（同形状，乱序，数值接近）
    if len(gold) == len(pred):
        # 按首列排序后比较
        try:
            gold_sorted = sorted([list(r) for r in gold], key=lambda x: str(x))
            pred_sorted = sorted([list(r) for r in pred], key=lambda x: str(x))
            if _rows_close(gold_sorted, pred_sorted, rtol):
                res.within_tolerance = True
                res.final_pass = True
                res.reason = "排序后容差一致"
                return res
        except Exception:
            pass

    res.reason = f"不匹配: gold_rows={len(gold)} pred_rows={len(pred)}"
    return res
