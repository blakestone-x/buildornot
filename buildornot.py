"""Should you build this side project? A yes/no with a confidence score.

Plain code gathers prior art from GitHub and Hacker News, one Jev request
judges the idea against it, and plain code makes the call. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
RUNS_DIR = Path(__file__).with_name("runs")
USER_AGENT = "buildornot/0.1"

# ---------------------------------------------------------------- the judgments
# Each Score runs low to high. `good` says which end favors building.

FIVE = lambda *levels: list(levels)  # noqa: E731  (keeps the level lists readable)

SCORES = {
    "pain": {
        "good": "high",
        "label": "How much the problem hurts",
        "instructions": "How painful is the problem in `idea` for the people in `for_whom`, judged only from what "
        "the description says or clearly implies?",
        "criteria": FIVE(
            "No real problem is described; it's a feature or a curiosity",
            "Mild annoyance people barely notice",
            "A real nuisance people complain about but live with",
            "A costly problem: lost time, money or missed results every time it happens",
            "A blocking problem people actively pay or hack around to fix",
        ),
    },
    "frequency": {
        "good": "high",
        "label": "How often people hit it",
        "instructions": "How often do the people in `for_whom` run into the problem in `idea`?",
        "criteria": FIVE(
            "Once, or almost never",
            "A few times a year",
            "Monthly",
            "Weekly",
            "Daily or constantly",
        ),
    },
    "audience": {
        "good": "high",
        "label": "How clearly you can name who it's for",
        "instructions": "How specifically do `idea` and `for_whom` identify the people who would use this?",
        "criteria": FIVE(
            "Everyone or no one in particular",
            "A broad group such as 'developers' or 'small businesses'",
            "A recognizable group with a shared situation",
            "A narrow group you could find and message this week",
            "Named people or communities, including the builder themself",
        ),
    },
    "solved": {
        "good": "low",
        "label": "How well it's already solved",
        "instructions": "Using `prior_art` and general knowledge of common tools, how well do existing tools or "
        "simple workarounds already solve the problem in `idea` for `for_whom`?",
        "criteria": FIVE(
            "Nothing addresses it",
            "Only clumsy manual workarounds",
            "Partial tools exist with clear gaps",
            "Good tools exist; this would need a sharp difference",
            "Popular, mature tools already do exactly this",
        ),
    },
    "feasible": {
        "good": "high",
        "label": "Whether you can build it",
        "instructions": "Given `builder` (skills, time, resources), how realistic is it for this builder to ship a "
        "first useful version of `idea`? If `builder` is empty, judge for one experienced developer working "
        "evenings for a month.",
        "criteria": FIVE(
            "Needs a team, research breakthroughs or resources far beyond the builder",
            "A long, uncertain project with major unknowns",
            "Doable with effort, with one or two hard unknowns",
            "Clearly doable; the hard parts are known techniques",
            "A weekend or two with tools the builder already uses",
        ),
    },
    "reach": {
        "good": "high",
        "label": "Whether you can reach the people it's for",
        "instructions": "Does the builder have a concrete way to get `idea` in front of `for_whom`? Use `builder` "
        "and `idea`; communities, existing users or the builder being a user themself all count.",
        "criteria": FIVE(
            "No channel is apparent",
            "Only generic launch posts",
            "A plausible channel, not yet tied to the builder",
            "A specific community or channel the builder is part of",
            "The builder already has the users, or is the user",
        ),
    },
}

WEIGHTS = {"pain": 0.24, "frequency": 0.14, "audience": 0.12, "solved": 0.20, "feasible": 0.18, "reach": 0.12}

DEALBREAKERS = {
    "no_problem": "Is `idea` a solution looking for a problem, with no concrete person or situation that needs it?",
    "blocked_access": "Does `idea` depend on data, APIs or access the builder is unlikely to get, such as private "
    "data, closed platforms or paid enterprise APIs?",
    "against_rules": "Would `idea` clearly break a platform's terms of service, the law, or people's privacy "
    "expectations as described?",
    "needs_crowd": "Is `idea` useless until it has a large number of users (a marketplace or social network "
    "cold start) with no plan in `idea` for the first users?",
}
DEALBREAKER_LABELS = {
    "no_problem": "Solution looking for a problem",
    "blocked_access": "Depends on access you probably can't get",
    "against_rules": "Breaks terms, law or privacy expectations",
    "needs_crowd": "Useless until it has lots of users",
}

# Policy knobs. Guesses until they're fit against real outcomes (see README).
DEALBREAKER_AT = 0.70
OVERLAP_AT = 0.75
TRACTION = {"github": 50, "hn": 50}  # stars / points that count as an established project
YES_AT, NO_AT = 0.60, 0.42

STOPWORDS = set(
    """a an and are as at be by can could do does for from get gets have how i if in into is it its just like
    lets make makes me my of on or our out so than that the their them then there these they this to tool
    tools up us use uses using want was we what when where which who why will with would you your app idea
    build building simple small new way thing things something help helps people command line cli file files
    convert converts converting turn turns turning based allows lets easy quick fast automatically automate
    automates automated generate generates create creates manage manages track tracks find finds""".split()
)

# ---------------------------------------------------------------- prior art


@dataclass
class Candidate:
    source: str  # "github" | "hn"
    title: str
    url: str
    description: str
    traction: int  # stars or points
    updated: str = ""
    overlap: float | None = None

    def for_jev(self) -> dict:
        kind = "GitHub repository" if self.source == "github" else "Hacker News post"
        unit = "stars" if self.source == "github" else "points"
        return {"kind": kind, "title": self.title, "description": self.description[:400], unit: self.traction}


def keywords(text: str, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}", text.lower()):
        word = word.strip(".-")
        if word not in STOPWORDS and len(word) > 2 and word not in seen:
            seen.append(word)
    return seen[:limit]


def get_json(url: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def search_github(query: str, limit: int) -> list[Candidate]:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        # Name and description only, in best-match order: sorting by stars surfaces keyword-stuffed spam.
        {"q": f"{query} in:name,description", "per_page": limit}
    )
    items = get_json(url, headers).get("items", [])
    return [
        Candidate("github", i["full_name"], i["html_url"], i.get("description") or "", i.get("stargazers_count", 0),
                  (i.get("pushed_at") or "")[:10])
        for i in items
    ]


def search_hn(query: str, limit: int) -> list[Candidate]:
    url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(
        {"query": query, "tags": "story", "hitsPerPage": limit}
    )
    hits = get_json(url).get("hits", [])
    return [
        Candidate("hn", h.get("title") or "", h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                  h.get("story_text") or "", h.get("points") or 0, (h.get("created_at") or "")[:10])
        for h in hits
        if h.get("title")
    ]


def find_prior_art(idea: str, query: str | None, notes: list[str]) -> list[Candidate]:
    words = query.split() if query else keywords(idea)
    found: dict[str, Candidate] = {}
    # A narrow query first, then a broader one, so a precise match isn't drowned out.
    for terms in (words[:4], words[:2]):
        if not terms:
            continue
        q = " ".join(terms)
        for search, limit in ((search_github, 6), (search_hn, 4)):
            try:
                for c in search(q, limit):
                    found.setdefault(c.url, c)
            except Exception as exc:  # a failed search lowers coverage; it doesn't stop the verdict
                notes.append(f"{search.__name__.removeprefix('search_')} search for '{q}' failed: {exc}")
    return list(found.values())[:12]


# ---------------------------------------------------------------- Jev


def api_key() -> str:
    if key := os.environ.get("TYPESAFE_API_KEY"):
        return key
    env = Path(__file__).with_name(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("Set TYPESAFE_API_KEY, or put it in a .env file next to buildornot.py.")


def build_request(idea: str, for_whom: str, builder: str, candidates: list[Candidate]) -> dict:
    questions: dict = {}
    for key, spec in SCORES.items():
        questions[key] = {"type": "score", "instructions": spec["instructions"], "criteria": spec["criteria"]}
    for key, text in DEALBREAKERS.items():
        questions[f"deal_{key}"] = {"type": "noul", "instructions": text}
    for n, c in enumerate(candidates):
        questions[f"overlap_{n}"] = {
            "type": "noul",
            "instructions": {
                "question": "Does `existing` already do what `idea` describes, for the same kind of user, closely "
                "enough that a user of it wouldn't need the new project?",
                "existing": c.for_jev(),
            },
            "criteria": {
                "true": "Same core job for the same users; differences are minor",
                "false": "Different job, different users, or only loosely related",
            },
        }
    return {
        "model": MODEL,
        "state": {
            "idea": idea,
            "for_whom": for_whom or "not stated",
            "builder": builder or "",
            "prior_art": [c.for_jev() for c in candidates] or "no prior art was found by search",
        },
        "questions": questions,
    }


def ask_jev(body: dict) -> dict:
    data = json.dumps(body).encode()
    request = urllib.request.Request(API_URL, data=data, method="POST", headers={
        "Authorization": f"Bearer {api_key()}", "Content-Type": "application/json", "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        sys.exit(f"Jev returned {exc.code}: {exc.read().decode(errors='replace')[:400]}")


# ---------------------------------------------------------------- the call


@dataclass
class Verdict:
    verdict: str  # "BUILD" | "MAYBE" | "DON'T BUILD"
    confidence: float
    composite: float
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    dealbreakers: list[str] = field(default_factory=list)
    overlaps: list[Candidate] = field(default_factory=list)
    dimensions: dict = field(default_factory=dict)


def decide(answers: dict, candidates: list[Candidate]) -> Verdict:
    dims = {}
    for key, spec in SCORES.items():
        a = answers[key]
        top = len(spec["criteria"]) - 1
        value = a["score"] / top if spec["good"] == "high" else 1 - a["score"] / top
        level = spec["criteria"][round(a["score"])]
        dims[key] = {"value": value, "confidence": a.get("confidence", 0.0), "level": level, "label": spec["label"]}
    composite = sum(WEIGHTS[k] * d["value"] for k, d in dims.items())

    for n, c in enumerate(candidates):
        c.overlap = answers[f"overlap_{n}"]["noul"]
    established = [c for c in candidates
                   if (c.overlap or 0) >= OVERLAP_AT and c.traction >= TRACTION[c.source]]
    if established:
        composite -= 0.08 + 0.04 * min(len(established) - 1, 2)

    breakers = [k for k in DEALBREAKERS if answers[f"deal_{k}"]["noul"] >= DEALBREAKER_AT]

    if breakers:
        verdict = "DON'T BUILD"
        strongest = max(answers[f"deal_{k}"]["noul"] for k in breakers)
        margin = (strongest - DEALBREAKER_AT) / (1 - DEALBREAKER_AT)
    elif composite >= YES_AT:
        verdict, margin = "BUILD", (composite - YES_AT) / (1 - YES_AT)
    elif composite <= NO_AT:
        verdict, margin = "DON'T BUILD", (NO_AT - composite) / NO_AT
    else:
        verdict = "MAYBE"
        middle = (YES_AT + NO_AT) / 2
        margin = 1 - abs(composite - middle) / ((YES_AT - NO_AT) / 2)
    # How far past the line, damped by how settled Jev's own answers were.
    jev_certainty = sum(d["confidence"] for d in dims.values()) / len(dims)
    confidence = max(0.05, min(0.95, 0.5 + 0.45 * min(margin, 1.0) * (0.5 + 0.5 * jev_certainty)))

    ranked = sorted(dims.values(), key=lambda d: d["value"])
    against = [f"{d['label']}: {d['level']}" for d in ranked if d["value"] < 0.45][:3]
    for_ = [f"{d['label']}: {d['level']}" for d in reversed(ranked) if d["value"] >= 0.6][:3]
    if established:
        against.insert(0, f"Already exists: {established[0].title} ({established[0].traction} "
                          f"{'stars' if established[0].source == 'github' else 'points'})")
    return Verdict(verdict, confidence, composite, for_, against,
                   [DEALBREAKER_LABELS[k] for k in breakers],
                   sorted([c for c in candidates if (c.overlap or 0) >= 0.5], key=lambda c: -(c.overlap or 0)),
                   dims)


# ---------------------------------------------------------------- output


def report(idea: str, v: Verdict, notes: list[str], searched: int) -> str:
    lines = [f"\n  {v.verdict}  ({v.confidence:.0%} confidence)\n", f"  {idea}\n"]
    if v.dealbreakers:
        lines.append("  Deal-breakers:")
        lines += [f"    x {d}" for d in v.dealbreakers]
    if v.reasons_for:
        lines.append("  For:")
        lines += [f"    + {r}" for r in v.reasons_for]
    if v.reasons_against:
        lines.append("  Against:")
        lines += [f"    - {r}" for r in v.reasons_against]
    lines.append(f"\n  Prior art ({searched} checked):")
    if v.overlaps:
        for c in v.overlaps[:5]:
            unit = "stars" if c.source == "github" else "points"
            lines.append(f"    {c.overlap:.0%} overlap  {c.title} ({c.traction} {unit})  {c.url}")
    else:
        lines.append("    nothing found that does the same job")
    for note in notes:
        lines.append(f"  note: {note}")
    lines.append("\n  Weights and thresholds are guesses until scored against real outcomes.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Should you build this side project?")
    parser.add_argument("idea", help="the idea, in a sentence or two")
    parser.add_argument("--for", dest="for_whom", default="", help="who it's for and the use case")
    parser.add_argument("--builder", default="", help="your skills, time and any audience you already have")
    parser.add_argument("--search", default=None, help="override the prior-art search terms")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):  # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    notes: list[str] = []
    candidates = find_prior_art(args.idea, args.search, notes)
    body = build_request(args.idea, args.for_whom, args.builder, candidates)
    response = ask_jev(body)
    verdict = decide(response["answers"], candidates)

    # Keep every run: these become the labeled set once outcomes are known.
    RUNS_DIR.mkdir(exist_ok=True)
    run_id = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    record = {"request": body, "response": response, "verdict": verdict.verdict,
              "confidence": verdict.confidence, "composite": verdict.composite, "outcome": None}
    (RUNS_DIR / f"{run_id}.json").write_text(json.dumps(record, indent=2))

    if args.json:
        print(json.dumps({**record, "run": run_id, "notes": notes}, indent=2))
    else:
        print(report(args.idea, verdict, notes, len(candidates)))
        usage = response.get("usage", {})
        print(f"  run {run_id} | {usage.get('input_tokens', 0)} input tokens\n")


if __name__ == "__main__":
    main()
