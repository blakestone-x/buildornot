"""Run the hand-labeled sanity cases against live Jev and report agreement.

These are judgment calls, not outcomes: they catch a policy or prompt change that
makes obviously bad verdicts, they don't prove calibration. Costs real tokens.

    python evals/run.py            # all cases
    python evals/run.py 3 7        # just these case numbers
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import buildornot as bon  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    cases = json.loads((ROOT / "evals" / "ideas.json").read_text(encoding="utf-8"))
    picked = {int(a) for a in sys.argv[1:]}
    results, tokens = [], 0
    for n, case in enumerate(cases, 1):
        if picked and n not in picked:
            continue
        try:
            verdict, record = bon.run(case["idea"], case["for"], case["builder"])
        except bon.BuildOrNotError as exc:
            print(f"{n:>2}  ERROR  {exc}")
            results.append({"n": n, "ok": False, "error": str(exc)})
            continue
        tokens += int(record["response"].get("usage", {}).get("input_tokens", 0))
        ok = verdict.verdict in case["accept"]
        flags = ", ".join(verdict.dealbreakers)
        print(f"{n:>2}  {'ok  ' if ok else 'MISS'}  {verdict.verdict:<11} {verdict.confidence:>4.0%}  "
              f"{case['idea'][:60]}{'  [' + flags + ']' if flags else ''}")
        results.append({"n": n, "ok": ok, "verdict": verdict.verdict, "confidence": verdict.confidence,
                        "composite": round(verdict.composite, 3), "accept": case["accept"], "queries": record["queries"]})
    agreed = sum(r["ok"] for r in results)
    print(f"\n{agreed}/{len(results)} within the accepted verdicts · ~{tokens} judgment input tokens")
    out = ROOT / "evals" / "results"
    out.mkdir(exist_ok=True)
    (out / f"{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if agreed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
