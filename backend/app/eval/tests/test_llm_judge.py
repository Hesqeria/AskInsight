"""L4 LLM Judge 单元测试：构造真实等价/不等价 SQL 对，验证 Judge 准确性"""
import sys
import os
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))

# 测试 6 个 case：3 等价 + 3 不等价
TEST_PAIRS = [
    # === 等价 case（应判 equivalent=True） ===
    {
        "name": "EQ_CASE1_子查询vs聚合",
        "gold": "SELECT COUNT(*) AS cnt FROM dw.dwd_order_info_inc",
        "pred": "SELECT COUNT(*) AS order_total FROM dw.dwd_order_info_inc",
        "gold_result": [(1000,)],
        "pred_result": [(1000,)],
        "expected": True,
    },
    {
        "name": "EQ_CASE2_字段顺序",
        "gold": "SELECT dt, gmv FROM dw.ads_gmv_total_day LIMIT 3",
        "pred": "SELECT gmv AS amount, dt AS day FROM dw.ads_gmv_total_day LIMIT 3",
        "gold_result": [('2026-08-01', 12345.0), ('2026-08-02', 23456.0)],
        "pred_result": [(12345.0, '2026-08-01'), (23456.0, '2026-08-02')],
        "expected": True,  # 字段顺序不同 + 列名不同，但业务等价
    },
    {
        "name": "EQ_CASE3_WHERE等价",
        "gold": "SELECT * FROM dw.dwd_order_info_inc WHERE order_status = '1001'",
        "pred": "SELECT * FROM dw.dwd_order_info_inc WHERE '1001' = order_status",
        "gold_result": [(1, 'paid',)],
        "pred_result": [(1, 'paid',)],
        "expected": True,
    },
    # === 不等价 case（应判 equivalent=False） ===
    {
        "name": "NE_CASE1_缺WHERE",
        "gold": "SELECT SUM(total_amount) FROM dw.dwd_order_info_inc WHERE order_status = '1001'",
        "pred": "SELECT SUM(total_amount) FROM dw.dwd_order_info_inc",
        "gold_result": [(5000.0,)],  # 已支付订单总和
        "pred_result": [(15000.0,)],  # 全部订单总和（包含未支付）
        "expected": False,
    },
    {
        "name": "NE_CASE2_缺GROUPBY",
        "gold": "SELECT user_level, COUNT(*) FROM dw.dim_user_info GROUP BY user_level",
        "pred": "SELECT COUNT(*) FROM dw.dim_user_info",
        "gold_result": [('L1', 100), ('L2', 50), ('L3', 20)],
        "pred_result": [(170,)],
        "expected": False,
    },
    {
        "name": "NE_CASE3_完全不同的表",
        "gold": "SELECT COUNT(*) FROM dw.dwd_order_info_inc",
        "pred": "SELECT COUNT(*) FROM dw.dwd_comment_info_inc",
        "gold_result": [(1000,)],
        "pred_result": [(500,)],
        "expected": False,
    },
]


def main():
    from app.eval.evaluator.l4_llm_judge import LLMJudge
    judge = LLMJudge(threshold=0.7)

    print(f"{'CASE':30s} {'期望':6s} {'实际':6s} {'通过':6s} {'置信度':8s} {'原因':30s}")
    print("-" * 100)

    pass_cnt = 0
    for case in TEST_PAIRS:
        verdict = judge.judge(
            gold_sql=case["gold"],
            pred_sql=case["pred"],
            gold_result=case["gold_result"],
            pred_result=case["pred_result"],
            pred_error="",
        )
        actual = verdict["equivalent"]
        expected = case["expected"]
        ok = "PASS" if actual == expected else "FAIL"
        if actual == expected:
            pass_cnt += 1
        print(f"{case['name']:30s} {str(expected):6s} {str(actual):6s} {ok:6s} "
              f"{verdict['confidence']:8.2f} {verdict['reason'][:30]}")

    print(f"\n准确率: {pass_cnt}/{len(TEST_PAIRS)} = {pass_cnt/len(TEST_PAIRS)*100:.1f}%")


if __name__ == "__main__":
    main()
