"""End-to-end benchmark: every scenario x {none, tripwire, llm_judge} x N runs.

    python -m bench.run_bench --runs 3

Writes bench/results.json and bench/results.md. Confirm prompts are answered by a
simulated *informed user* who approves only recipients the user actually meant.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from demo.agent import JUDGE_MODEL, REGION, informed_user_approver, run_scenario
from demo.scenarios import SCENARIOS
from tripwire import Decider
from tripwire.llm_judge import LlmJudgeGuard

OUT = Path(__file__).parent


def pct(xs: list[bool]) -> str:
    return f"{100 * sum(xs) / len(xs):.0f}%" if xs else "-"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--modes", default="none,tripwire,guard_only,llm_judge")
    a = p.parse_args()
    modes = a.modes.split(",")

    decider = Decider()
    judge = LlmJudgeGuard(JUDGE_MODEL, REGION) if "llm_judge" in modes else None
    rows = []
    for sc in SCENARIOS:
        for mode in modes:
            for i in range(a.runs):
                kw = {"approver": informed_user_approver(sc)}
                if mode in ("tripwire", "guard_only"):
                    kw["decider"] = decider
                if mode == "llm_judge":
                    kw["judge"] = judge
                try:
                    r = run_scenario(sc, mode, **kw)
                except Exception as exc:  # keep the benchmark going; record the failure
                    print(f"{sc.id} {mode} run {i}: ERROR {exc}")
                    continue
                rows.append(r.__dict__)
                print(f"{sc.id:20} {mode:9} run {i}: attack={r.attack_succeeded} task={r.task_completed} "
                      f"guards={len(r.guard_log)} chunks={len(r.quarantine_log)} {r.wall_s:.1f}s")

    (OUT / "results.json").write_text(json.dumps(rows, indent=2, default=str))

    lines = ["| mode | attack success (lower is better) | benign tasks completed | guard decisions | median guard latency |",
             "|---|---|---|---|---|"]
    for mode in modes:
        mr = [r for r in rows if r["mode"] == mode]
        atk = [r["attack_succeeded"] for r in mr if r["kind"] == "attack"]
        ben = [r["task_completed"] for r in mr if r["kind"] == "benign"]
        lat = [d["latency_ms"] for r in mr for d in r["guard_log"]]
        med = f"{statistics.median(lat):.0f} ms" if lat else "-"
        lines.append(f"| {mode} | {pct(atk)} ({sum(atk)}/{len(atk)}) | {pct(ben)} ({sum(ben)}/{len(ben)}) | {len(lat)} | {med} |")

    chunk_lat = [c["latency_ms"] for r in rows for c in r["quarantine_log"]]
    lines.append("")
    if chunk_lat:
        lines.append(f"Quarantine: {len(chunk_lat)} chunk decisions, median {statistics.median(chunk_lat):.0f} ms.")
    if judge:
        lines.append(f"LLM judge ({JUDGE_MODEL}): {judge.input_tokens} in / {judge.output_tokens} out tokens, "
                     f"${judge.cost_usd():.4f} total; Decider runs locally at $0 per decision.")
    md = "\n".join(lines)
    (OUT / "results.md").write_text(md + "\n")
    print("\n" + md)


if __name__ == "__main__":
    main()
