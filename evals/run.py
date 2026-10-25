"""Run the hand-labeled sanity cases against live Jev and report agreement.

These are judgment calls, not outcomes: they catch a policy or prompt change that
makes obviously bad verdicts, they don't prove calibration. Costs real tokens.

    python evals/run.py            # all cases
    python evals/run.py 3 7        # just these case numbers

Without GITHUB_TOKEN it pauses between cases to stay under GitHub's unauthenticated search limit.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import buildornot as bon  # noqa: E402


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cases = json.loads((ROOT / "evals" / "ideas.json").read_text(encoding="utf-8"))
    picked = {int(a) for a in sys.argv[1:]}
    pause = 0 if os.environ.get("GITHUB_TOKEN") else 13  # up to 2 GitHub searches per case, 10 allowed a minute
    results: list[dict[str, object]] = []
    tokens = 0
    for n, case in enumerate(cases, 1):
        if picked and n not in picked:
            continue
        if results and pause:
            time.sleep(pause)
        try:
            verdict, record = bon.run(case["idea"], case["for"], case["builder"])
        except bon.BuildOrNotError as exc:
            print(f"{n:>2}  ERROR  {exc}")
            results.append({"n": n, "ok": False, "error": str(exc)})
            continue
        tokens += record["input_tokens"]
        ok = verdict.verdict in case["accept"]
        flags = ", ".join(verdict.dealbreakers + verdict.held_back)
        print(f"{n:>2}  {'ok  ' if ok else 'MISS'}  {verdict.verdict:<11} {verdict.strength:<10}  "
              f"{case['idea'][:56]}{'  [' + flags + ']' if flags else ''}")
        results.append({"n": n, "ok": ok, "verdict": verdict.verdict, "strength": verdict.strength,
                        "composite": round(verdict.composite, 3), "accept": case["accept"],
                        "dealbreakers": verdict.dealbreakers, "held_back": verdict.held_back,
                        "queries": record["queries"], "notes": record["notes"]})
    agreed = sum(1 for r in results if r["ok"])
    print(f"\n{agreed}/{len(results)} within the accepted verdicts, {tokens} Jev input tokens")
    out = ROOT / "evals" / "results"
    out.mkdir(exist_ok=True)
    (out / f"{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if agreed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
