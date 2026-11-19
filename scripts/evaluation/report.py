"""Aggregate saved results without making any model or provider calls."""

from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import sys

from scripts.evaluation.ledger import Budget
from scripts.evaluation.scoring import score_turn


def percentile(values, q):
    return round(sorted(values)[max(0, math.ceil(len(values) * q) - 1)], 3) if values else None


def main(directory):
    corpus = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["corpus"]
    rows = json.loads((directory / "conversations.json").read_text(encoding="utf-8"))
    sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
    judges = json.loads((directory / "judgments.json").read_text(encoding="utf-8"))
    browser = json.loads((directory / "browser.json").read_text(encoding="utf-8"))
    replay = json.loads((directory / "browser-replay.json").read_text(encoding="utf-8"))
    expected = {c["id"]: c for c in corpus["input_cases"]}
    scored = []
    for case in rows:
        prior = None
        checks = []
        for row, gold in zip(case.get("turns", []), expected[case["id"]]["expected"]):
            checks.append(score_turn(row, gold, prior))
            prior = row
        scored.append(dict(id=case["id"], category=case["category"], checks=checks))
    turns = [turn for case in rows for turn in case.get("turns", [])]
    passed = sum(c["passed"] for case in scored for c in case["checks"])
    failed = sum(c["failed"] for case in scored for c in case["checks"])
    qualified_cases = [
        case for case in scored if sum(c["passed"] + c["failed"] for c in case["checks"])
    ]
    factual_case_pass = sum(
        all(c["failed"] == 0 for c in case["checks"]) for case in qualified_cases
    )
    dimensions = {}
    for dimension in ["next_step", "no_repetition", "task_completion", "recovery", "honesty"]:
        values = [
            r["scores"][dimension] for r in judges if r.get("scores", {}).get(dimension) is not None
        ]
        dimensions[dimension] = dict(
            mean=round(statistics.mean(values), 3) if values else None, count=len(values), scale=4
        )
    metrics = dict(
        created_at=datetime.now().isoformat(),
        planned_cases=24,
        planned_turns=32,
        executed_turns=len(turns),
        successful_turns=sum(t["status"] == "succeeded" for t in turns),
        factual_assertions=dict(passed=passed, total=passed + failed),
        factual_cases=dict(
            passed=factual_case_pass, total=len(qualified_cases), unscored=24 - len(qualified_cases)
        ),
        source_cases=len(sources),
        search_with_results=sum(r.get("hotel_count", 0) > 0 for r in sources),
        hotels=sum(r.get("hotel_count", 0) for r in sources),
        detail_with_quotes=sum(r.get("quote_count", 0) > 0 for r in sources),
        reference_quotes=sum(r.get("quote_count", 0) for r in sources),
        map_responses=sum(r.get("map_status") == "complete" for r in sources),
        unique_anchors=sum(r.get("anchor_unique_exact", False) for r in sources),
        walk_attempts=sum("walk_status" in r for r in sources),
        successful_walks=sum(r.get("walk_status") == "complete" for r in sources),
        nearby_candidates=sum(r.get("nearby_count", 0) for r in sources),
        matched_candidates=sum(r.get("matched_count", 0) for r in sources),
        bookable_cases=sum(r.get("bookable", False) for r in sources),
        priced_nearby_route_cases=sum(
            r.get("walk_price_linked") is True and r.get("walk_status") == "complete"
            for r in sources
        ),
        latency_seconds={
            name: dict(
                n=len(values),
                p50=round(statistics.median(values), 3),
                p95_nearest_rank=percentile(values, 0.95),
            )
            for name, values in {
                "llm_turn": [t["seconds"] for t in turns if "seconds" in t],
                "hotel_search": [r["search_seconds"] for r in sources if "search_seconds" in r],
                "hotel_detail": [r["detail_seconds"] for r in sources if "detail_seconds" in r],
                "map_resolve": [r["resolve_seconds"] for r in sources if "resolve_seconds" in r],
                "walk": [r["walk_seconds"] for r in sources if "walk_seconds" in r],
            }.items()
            if values
        },
        judge_count=sum("scores" in j for j in judges),
        judge_dimensions=dimensions,
        browser_real_turns=sum(len(r["turns"]) for r in browser),
        browser_real_success=sum(t["status"] == "succeeded" for r in browser for t in r["turns"]),
        browser_real_full_handoff_pass=sum(bool(r.get("handoffPassed")) for r in browser),
        browser_driver_failures=sum("driver_error" in r for r in browser),
        validated_replay_handoff_pass=sum(r["passed"] for r in replay),
        budget=Budget(directory / "budget.sqlite").summary(),
    )
    (directory / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = """# R106 自动化多维评测结果（2026-11-16）

## 结论

真实接口多数能返回数据，但“给用户找到目的地附近、带同店参考价并核实路线的酒店”本轮未完成。对话层发现人数分房歧义、口语解析失败，以及境外范围提示缺失。点评咨询与接入继续按用户决定搁置；本轮没有把未知点评算作业务故障或伪造数据。

## 运行与授权

基线main/c5d8bee，实施分支codex/r106-automatic-evaluation。用户明确批准原始HTTP上限DeepSeek80、道旅48、高德18、腾讯12，其他0。唯一运行目录runtime/r106-20261116-a；独立模型会话库、酒店会话库与SQLite发送前账本，不修改个人运行库。未下单、未打开来源预订链接、未申请账号或咨询供应商。

24个合成对话案例（16单轮、8双轮，共32轮）；6个国内城市/锚点案例；8类异常使用现有代码回归核验。所有测试日期和预期均在调用前固定于该运行manifest。没有随机重抽直到成功、没有用剩余地图额度换道旅额度，未重跑历史真实探针。

## 客观结果

| 指标 | 实测 | 解读 |
|---|---:|---|
"""

    def ratio(n, d):
        return f"{n}/{d}（{n / d:.1%}）" if d else "未执行"

    report += f"""| 对话应用处理成功 | {ratio(metrics["successful_turns"], 32)} | 不等于语义全部正确 |
| 确定性字段/状态断言 | {ratio(passed, passed + failed)} | 缺失或错误不能从分母移除 |
| 有数值断言的案例全通过 | {ratio(factual_case_pass, len(qualified_cases))} | S14只有定性预期，另作范围提示评审，不记为客观通过 |
| 城市酒店查询有结果 | {ratio(metrics["search_with_results"], 6)} | 共{metrics["hotels"]}家；单一未来日期，未设预算上限，不是低预算成功率 |
| 房型详情有报价 | {ratio(metrics["detail_with_quotes"], 6)} | 共{metrics["reference_quotes"]}条参考方案，不是可订库存 |
| 地图地点响应 | {ratio(metrics["map_responses"], 6)} | 均有候选，不等于已选对具体分店 |
| 锚点唯一精确匹配 | {ratio(metrics["unique_anchors"], 6)} | 上海人民广场未唯一精确对应，停止其附近/路线步骤 |
| 路线返回 | {ratio(metrics["successful_walks"], metrics["walk_attempts"])} | 按全部计划案例计为{ratio(metrics["successful_walks"], 6)}，不能移除被阻断案例后宣称全流程100% |
| 地图候选与报价同店匹配 | {ratio(metrics["matched_candidates"], metrics["nearby_candidates"])} | 5组附近检索；匹配失败原因尚未区分覆盖差异与名址差异 |
| 目的地附近带价且路线可核实的完整案例 | 0/6 | 当前候选链路的主要业务短板；不与地图单源路线成功混为一谈 |
| 最终可订证据 | 0/6 | 仍是已知协议缺口，不承诺本轮能解决 |

"""
    report += "## 各城市真实结果\n\n| 城市/锚点 | 酒店数 | 参考报价数 | 附近候选/同店匹配 | 步行 |\n|---|---:|---:|---:|---|\n"
    for row in sources:
        report += f"| {row['city']}／{row['anchor']} | {row.get('hotel_count', '未执行')} | {row.get('quote_count', '未执行')} | {row.get('nearby_count', '未执行')}／{row.get('matched_count', '未执行')} | {str(row.get('walk_metres')) + '米' if row.get('walk_status') == 'complete' else row.get('remaining_map_steps', '未执行')} |\n"
    report += """
## 已发现的问题（未修改产品行为）

1. **P1 · 多房总人数误作每房人数（S05）。** 原输入“2间房，一共3个大人”，系统保存rooms=2、adults=3（业务字段是每房成人），query_ready=true。应澄清分配，不能把总3人当作每房3人。
2. **P1 · 附近酒店未形成同店带价闭环（L01～L06）。** 城市报价查询成功与地图附近成功互不替代。本轮50个地图酒店候选中0个与当前城市报价成员严格同店关联。先分析召回覆盖/名址差异，不能通过放宽身份门禁来刷高成功率。
3. **P2 · 口语输入整轮失败（S15）。** “住一宿、就一间、两位大人、没小孩儿、三百块”等完整条件触发INVALID_MODEL_OUTPUT，草稿未提交。这里只确认应用输出校验失败，尚不能定位为哪一个口语词或单纯模型故障；未追加同例真实重试。
4. **P2 · 境外范围未在对话层提示（S14）。** 东京新宿被整理为query_ready=true，提示可填表，未说明仅支持中国大陆。实际没有对该案例执行酒店/地图请求，因此不能据此断言已发生境外调用或已绕过来源地区门禁。

## LLM体验代理评审

DeepSeek独立会话按固定量表评价24个案例；不是独立模型，不是真人满意度。每项0～4分，不适用为null且不纳入该维度均值。LLM结论只作体验意见，不决定酒店身份、可订事实或客观成功率。评审确认S05/S14/S15的问题；全部判断与证据在运行目录judgments.json。

| 维度 | 均分/4 | 有效案例数 |
|---|---:|---:|
"""
    labels = {
        "next_step": "下一步是否清楚",
        "no_repetition": "是否避免重复询问",
        "task_completion": "任务完成",
        "recovery": "错误恢复",
        "honesty": "限制/未知表达",
    }
    for k, v in dimensions.items():
        report += f"| {labels[k]} | {v['mean']} | {v['count']} |\n"
    report += """
## 浏览器体验与测试工具问题

Edge桌面1440与手机390实际完成4轮真实模型调用，全部succeeded；无JS页面错误、无横向溢出、无酒店/地图/外站页面请求。

原脚本沿用已被替换的控件ID `natural-restaurant-anchor`，在填表交接处两端均TimeoutError。因此首次真实浏览器完整交接为0/2，不能报告为真实端到端全通过。已修正驱动为当前 `natural-stay-anchor`，没有追加模型调用。另从本轮独立会话数据库恢复真实后端验证过的query、missing、warnings做离线回放，两端填表交接2/2通过；它只验证已保存状态的UI交接，不替代首次真实运行。

第一次回放手机端未勾选附加条件知悉，脚本超时；已补充显式勾选后完成。回放不请求真实模型或供应商。截图browser-*.png及replay-*.png在运行目录，已人工查看。

## 8类离线异常覆盖

空结果/超时/取消/旧响应：App.test.tsx；429/断网：ApiErrors.test.ts；120秒过期与无点评不能排名：test_poi_nearby.py、NearbyExplorer.test.tsx、nearbyRankingR105.test.ts、test_review_evidence_r105.py。 targeted回归前端52项、后端38项通过（其中评测驱动初版4项；审查后另加2项专项，总驱动6项通过）。这里是确定性模拟覆盖，不宣称真实断网/限流曾发生。

## 耗时（秒，p95采用nearest-rank，小样本不作总体估计）

| 流程 | 样本数 | p50 | p95 |
|---|---:|---:|---:|
"""
    for name, values in metrics["latency_seconds"].items():
        report += f"| {name} | {values['n']} | {values['p50']} | {values['p95_nearest_rank']} |\n"
    report += (
        "\n## 原始HTTP账本\n\n| 来源 | 实耗/上限 | 收到响应 | HTTP 2xx |\n|---|---:|---:|---:|\n"
    )
    for name, row in metrics["budget"].items():
        report += f"| {name} | {row['attempted']}/{row['limit']} | {row['http_received']} | {row['http_ok']} |\n"
    report += """
所有实发HTTP均返回2xx，但对话语义和同店闭环仍有失败，正说明不能用HTTP成功率代替产品成功率。发送前原子计数含握手、修复和失败尝试；其他来源0。实际金额未从账单核实，不臆测费用。道旅已到48/48，其余剩余额度不自动续测，也不跨来源挪用。

## 独立审查与修正

独立审查指出评测工具三项问题并已离线修正：缺失事实字段必须记失败，不能进入未评列表；401/402/403/429在账本finish中原子停止来源；回放必须使用本轮原会话的实际验证状态，不能从draft伪造可提交query。上述HTTP停止问题未影响本次数据（实际均2xx），新增跨进程停止及缺字段断言测试通过。

## 产物、限制与下一步

本报告及静态测试集/评测脚本可进Git；runtime内会话、账本、逐轮脱敏结果和截图留本机，不上传。输入均为合成例子，不是个人行程；不保存凭据/原始供应商响应。单一模型、每案例一次、不重复采样，6个城市/一个日期，不能推算全国覆盖、长期稳定性或真人满意度。

建议按顺序修复人数分房、口语/范围提示，再改善附近酒店召回与严格同店关联可观测性；后续真实回归重新登记目标和预算。点评咨询和接入不重新开启，个人0.9标签不移动。
"""
    (directory / "evaluation-report.md").write_text(report, encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
