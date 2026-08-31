# -*- coding: utf-8 -*-
"""
eval_loss_risk.py — 资损陷阱题跑批（2026-08-31）
====================================================================
纯规则模式（零 LLM 零 key，回复确定性生成），逐题跑断言：
  PASS/FAIL 表 + 总体拒绝率，报告落 data/loss_risk_report.csv。
全过退出码 0（CI 可挂）——资损防护是架构级回归门，不是一次性演示。

用法：python eval_loss_risk.py
"""
import csv
import io
import sys
from pathlib import Path

if not getattr(sys, "_beauty_stdout_utf8", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys._beauty_stdout_utf8 = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import GuideAgent
from loss_risk_cases import CASES_LOSS_RISK

ROOT = Path(__file__).resolve().parent.parent


def main():
    zh = GuideAgent(intent_mode="rule", reply_lang="zh")
    en = GuideAgent(intent_mode="rule", reply_lang="en")

    n_pass = n_fail = 0
    rows = []
    print(f"资损陷阱题跑批（纯规则模式，零 LLM 零 key）：{len(CASES_LOSS_RISK)} 题\n")
    for c in CASES_LOSS_RISK:
        agent = zh if c.get("lang", "zh") == "zh" else en
        rec = agent.run(c["query"])
        fails = []
        for name, fn in c["asserts"]:
            ok, why = fn(rec, agent)
            if not ok:
                fails.append(f"{name}: {why}")
        passed = not fails
        n_pass += passed
        n_fail += not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {c['id']} {c['cat']}（{c.get('lang', 'zh')}）")
        print(f"      query: {c['query']}")
        if fails:
            for f in fails:
                print(f"      ✗ {f}")
        else:
            print(f"      ✓ {c['desc']}")
        rows.append([c["id"], c["cat"], c.get("lang", "zh"), c["query"],
                     "PASS" if passed else "FAIL",
                     "; ".join(fails) if fails else ""])

    rate = n_pass / len(CASES_LOSS_RISK) * 100
    print(f"\n结果：{n_pass}/{len(CASES_LOSS_RISK)} 通过，拒绝率 {rate:.1f}%")
    out = ROOT / "data" / "loss_risk_report.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "cat", "lang", "query", "result", "fail"])
        w.writerows(rows)
    print(f"已写 {out}")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
