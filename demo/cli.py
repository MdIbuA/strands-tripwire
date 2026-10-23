"""Run one scenario with and without Tripwire in the terminal.

    python -m demo.cli finance_portal
    python -m demo.cli --list
"""

from __future__ import annotations

import argparse
import json
import sys

from .agent import run_scenario
from .scenarios import BY_ID, SCENARIOS

RED, GREEN, YELLOW, DIM, BOLD, END = "\033[91m", "\033[92m", "\033[93m", "\033[2m", "\033[1m", "\033[0m"


def emit(kind: str, d: dict) -> None:
    if kind == "tool_proposed":
        print(f"  {DIM}agent proposes{END} {d['tool']}({json.dumps(d['args'])[:120]})")
    elif kind == "chunk_verdict":
        tag = f"{RED}QUARANTINED{END}" if d["quarantined"] else f"{GREEN}clean{END}"
        print(f"    {DIM}decider{END} chunk {d['index']} injection={d['injection']:.2f} "
              f"[{d['latency_ms']:.0f} ms] {tag}")
    elif kind == "guard_decision":
        color = {"proceed": GREEN, "approved": GREEN, "deny": RED, "rejected": RED}.get(d["verdict"], YELLOW)
        print(f"    {DIM}decider{END} P(user asked for this)={d['asked']:.2f} "
              f"[{d['latency_ms']:.0f} ms] -> {color}{d['verdict'].upper()}{END} {DIM}{d['reason']}{END}")
    elif kind == "side_effect":
        print(f"  {BOLD}EXECUTED{END} {d['action']} -> {', '.join(d['recipients'])}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scenario", nargs="?", default="finance_portal")
    p.add_argument("--mode", choices=["none", "tripwire", "both"], default="both")
    p.add_argument("--list", action="store_true")
    a = p.parse_args()
    if a.list:
        for s in SCENARIOS:
            print(f"{s.id:20} {s.kind:7} {s.title}")
        return 0
    sc = BY_ID[a.scenario]
    print(f"\n{BOLD}{sc.title}{END}  ({sc.kind})\nUSER: {sc.user_prompt}\n")
    for mode in (["none", "tripwire"] if a.mode == "both" else [a.mode]):
        print(f"{BOLD}=== {'UNPROTECTED' if mode == 'none' else 'TRIPWIRE'} ==={END}")
        r = run_scenario(sc, mode, emit=emit)
        if sc.kind == "attack":
            print(f"{RED}ATTACK SUCCEEDED{END}" if r.attack_succeeded else f"{GREEN}attack blocked{END}")
        print(f"task completed: {r.task_completed}   ({r.wall_s:.1f}s)")
        print(f"{DIM}agent: {r.final_text.strip()[:600]}{END}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
