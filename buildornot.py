"""Should you build this side project? A yes/no with a confidence score.

Plain code gathers prior art from GitHub and Hacker News, Jev judges the idea
against it, and plain code makes the call. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

__version__ = "0.2.0"

API_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
HOME = Path(os.environ.get("BUILDORNOT_HOME") or Path.home() / ".buildornot")
USER_AGENT = f"buildornot/{__version__}"
MAX_IDEA_CHARS = 2000

Json = dict[str, Any]


class BuildOrNotError(Exception):
    """A problem the user can fix; printed without a traceback."""


# ---------------------------------------------------------------- the judgments
# Each Score runs low to high. `good` says which end favors building.

SCORES: dict[str, Json] = {
    "pain": {
        "good": "high",
        "label": "How much the problem hurts",
        "instructions": "How painful is the problem in `idea` for the people in `for_whom`, judged only from what "
        "the description says or clearly implies?",
        "criteria": [
            "No real problem is described; it's a feature or a curiosity",
            "Mild annoyance people barely notice",
            "A real nuisance people complain about but live with",
            "A costly problem: lost time, money or missed results every time it happens",
            "A blocking problem people actively pay or hack around to fix",
        ],
    },
    "frequency": {
        "good": "high",
        "label": "How often people hit it",
        "instructions": "How often do the people in `for_whom` run into the problem in `idea`?",
        "criteria": ["Once, or almost never", "A few times a year", "Monthly", "Weekly", "Daily or constantly"],
    },
    "audience": {
        "good": "high",
        "label": "How clearly you can name who it's for",
        "instructions": "How specifically do `idea` and `for_whom` identify the people who would use this?",
        "criteria": [
            "Everyone or no one in particular",
            "A broad group such as 'developers' or 'small businesses'",
            "A recognizable group with a shared situation",
            "A narrow group you could find and message this week",
            "Named people or communities, including the builder themself",
        ],
    },
    "solved": {
        "good": "low",
        "label": "How well it's already solved",
        "instructions": "Using `prior_art` and general knowledge of common tools, how well do existing tools or "
        "simple workarounds already solve the problem in `idea` for `for_whom`?",
        "criteria": [
            "Nothing addresses it",
            "Only clumsy manual workarounds",
            "Partial tools exist with clear gaps",
            "Good tools exist; this would need a sharp difference",
            "Popular, mature tools already do exactly this",
        ],
    },
    "feasible": {
        "good": "high",
        "label": "Whether you can build it",
        "instructions": "Given `builder` (skills, time, resources), how realistic is it for this builder to ship a "
        "first useful version of `idea`? If `builder` is empty, judge for one experienced developer working "
        "evenings for a month.",
        "criteria": [
            "Needs a team, research breakthroughs or resources far beyond the builder",
            "A long, uncertain project with major unknowns",
            "Doable with effort, with one or two hard unknowns",
            "Clearly doable; the hard parts are known techniques",
            "A weekend or two with tools the builder already uses",
        ],
    },
    "reach": {
        "good": "high",
        "label": "Whether you can reach the people it's for",
        "instructions": "Does the builder have a concrete way to get `idea` in front of `for_whom`? Use `builder` "
        "and `idea`; communities, existing users or the builder being a user themself all count.",
        "criteria": [
            "No channel is apparent",
            "Only generic launch posts",
            "A plausible channel, not yet tied to the builder",
            "A specific community or channel the builder is part of",
            "The builder already has the users, or is the user",
        ],
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
# Some lows can't be made up for elsewhere: no problem, or a project far beyond the builder.
# Score positions (0 to 4) below these end the verdict like a deal-breaker.
FLOORS = {"pain": (0.75, "No real problem described"), "feasible": (0.75, "Far beyond what you can build")}
OVERLAP_AT = 0.75
TRACTION = {"github": 50, "hn": 50}  # stars / points that count as an established project
YES_AT, NO_AT = 0.60, 0.42

STOPWORDS = set(
    """a an and are as at be by can could do does for from get gets have how i if in into is it its just like
    lets make makes me my of on or our out so than that the their them then there these they this to tool
    tools up us use uses using want was we what when where which who why will with would you your app idea
    build building simple small new way thing things something help helps people command line cli file files
    convert converts converting turn turns turning based allows easy quick fast automatically automate
    automates automated generate generates create creates manage manages track tracks find finds""".split()
)

# ---------------------------------------------------------------- HTTP


def request_json(url: str, *, body: Json | None = None, headers: dict[str, str] | None = None,
                 timeout: float = 20, retries: int = 2) -> Json:
    """GET or POST JSON, retrying rate limits and server errors with backoff."""
    data = json.dumps(body).encode() if body is not None else None
    all_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if data is not None:
        all_headers["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=all_headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            detail = exc.read().decode(errors="replace")[:300]
            raise HttpError(exc.code, detail) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HttpError(0, str(getattr(exc, "reason", exc))) from exc
    raise HttpError(0, "no response")


class HttpError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}" if status else detail)
        self.status = status


# ---------------------------------------------------------------- Jev


def api_key() -> str:
    if key := os.environ.get("TYPESAFE_API_KEY"):
        return key
    for env in (Path.cwd() / ".env", HOME / ".env"):
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("TYPESAFE_API_KEY="):
                    if value := line.split("=", 1)[1].strip().strip("'\""):
                        return value
    raise BuildOrNotError(
        f"No TypeSafe API key. Set TYPESAFE_API_KEY, or add TYPESAFE_API_KEY=... to {HOME / '.env'} "
        "(get a key at https://typesafe.ai)."
    )


def ask_jev(state: Json, questions: Json) -> Json:
    try:
        return request_json(API_URL, body={"model": MODEL, "state": state, "questions": questions},
                            headers={"Authorization": f"Bearer {api_key()}"}, timeout=90)
    except HttpError as exc:
        if exc.status in (401, 403):
            raise BuildOrNotError("TypeSafe rejected the API key. Check TYPESAFE_API_KEY.") from exc
        raise BuildOrNotError(f"Jev request failed: {exc}") from exc


# ---------------------------------------------------------------- prior art


@dataclass
class Candidate:
    source: Literal["github", "hn"]
    title: str
    url: str
    description: str
    traction: int  # stars or points
    updated: str = ""
    overlap: float | None = None

    @property
    def unit(self) -> str:
        return "stars" if self.source == "github" else "points"

    def for_jev(self) -> Json:
        kind = "GitHub repository" if self.source == "github" else "Hacker News post"
        return {"kind": kind, "title": self.title, "description": self.description[:400], self.unit: self.traction}


def words_of(text: str) -> list[str]:
    return [w.strip(".-") for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}", text.lower())]


def keywords(text: str, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for word in words_of(text):
        if word not in STOPWORDS and len(word) > 2 and word not in seen:
            seen.append(word)
    return seen[:limit]


def candidate_terms(idea: str, limit: int = 16) -> list[str]:
    """Single words and adjacent pairs from the idea, as search-term candidates for Jev to pick from."""
    words = words_of(idea)
    terms: list[str] = []
    for i, word in enumerate(words):
        if word in STOPWORDS or len(word) < 3:
            continue
        if word not in terms:
            terms.append(word)
        if i + 1 < len(words) and words[i + 1] not in STOPWORDS and len(words[i + 1]) >= 3:
            pair = f"{word} {words[i + 1]}"
            if pair not in terms:
                terms.append(pair)
    return terms[:limit]


def pick_search_terms(idea: str, notes: list[str]) -> list[str]:
    """Select, don't generate: Jev rates each candidate term, code builds the queries."""
    terms = candidate_terms(idea)
    if len(terms) < 2:
        return [" ".join(terms)] if terms else []
    questions = {
        f"term_{n}": {
            "type": "noul",
            "instructions": {
                "question": "Would a developer looking for an existing project that does `idea` type `term` into a "
                "GitHub search, because it names what the project is or does?",
                "term": term,
            },
        }
        for n, term in enumerate(terms)
    }
    try:
        answers = ask_jev({"idea": idea}, questions)["answers"]
    except BuildOrNotError as exc:
        notes.append(f"picking search terms failed, used plain keywords ({exc})")
        return [" ".join(keywords(idea)[:3])]
    ranked = sorted(terms, key=lambda t: -answers[f"term_{terms.index(t)}"]["noul"])
    best: list[str] = []
    for term in ranked:  # the two strongest terms that don't repeat a word
        if not any(set(term.split()) & set(b.split()) for b in best):
            best.append(term)
        if len(best) == 2:
            break
    queries = [" ".join(best), ranked[0]]
    return list(dict.fromkeys(q for q in queries if q))


def search_github(query: str, limit: int) -> list[Candidate]:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        # Name and description only, in best-match order: sorting by stars surfaces keyword-stuffed spam.
        {"q": f"{query} in:name,description", "per_page": limit}
    )
    items = request_json(url, headers=headers, retries=0).get("items", [])
    return [
        Candidate("github", i["full_name"], i["html_url"], i.get("description") or "", i.get("stargazers_count", 0),
                  (i.get("pushed_at") or "")[:10])
        for i in items
    ]


def search_hn(query: str, limit: int) -> list[Candidate]:
    url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(
        {"query": query, "tags": "story", "hitsPerPage": limit}
    )
    hits = request_json(url, retries=1).get("hits", [])
    return [
        Candidate("hn", h.get("title") or "", h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                  h.get("story_text") or "", h.get("points") or 0, (h.get("created_at") or "")[:10])
        for h in hits
        if h.get("title")
    ]


def find_prior_art(queries: list[str], notes: list[str]) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    for q in queries:
        for name, search, limit in (("GitHub", search_github, 6), ("Hacker News", search_hn, 4)):
            try:
                for c in search(q, limit):
                    found.setdefault(c.url, c)
            except HttpError as exc:  # a failed search lowers coverage; it doesn't stop the verdict
                hint = " (set GITHUB_TOKEN to raise the rate limit)" if exc.status in (403, 429) else ""
                notes.append(f"{name} search for '{q}' failed: {exc}{hint}")
    return list(found.values())[:12]


# ---------------------------------------------------------------- the judgment request


def build_request(idea: str, for_whom: str, builder: str, candidates: list[Candidate]) -> tuple[Json, Json]:
    questions: Json = {}
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
    state = {
        "idea": idea,
        "for_whom": for_whom or "not stated",
        "builder": builder or "",
        "prior_art": [c.for_jev() for c in candidates] or "no prior art was found by search",
    }
    return state, questions


# ---------------------------------------------------------------- the call


@dataclass
class Verdict:
    verdict: Literal["BUILD", "MAYBE", "DON'T BUILD"]
    confidence: float
    composite: float
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    dealbreakers: list[str] = field(default_factory=list)
    overlaps: list[Candidate] = field(default_factory=list)
    dimensions: Json = field(default_factory=dict)


def decide(answers: Json, candidates: list[Candidate]) -> Verdict:
    dims: Json = {}
    for key, spec in SCORES.items():
        a = answers[key]
        top = len(spec["criteria"]) - 1
        position = min(max(float(a["score"]), 0.0), float(top))
        value = position / top if spec["good"] == "high" else 1 - position / top
        dims[key] = {"value": value, "confidence": float(a.get("confidence", 0.0)),
                     "level": spec["criteria"][round(position)], "label": spec["label"]}
    composite = sum(WEIGHTS[k] * d["value"] for k, d in dims.items())

    for n, c in enumerate(candidates):
        c.overlap = float(answers[f"overlap_{n}"]["noul"])
    established = sorted((c for c in candidates
                          if (c.overlap or 0) >= OVERLAP_AT and c.traction >= TRACTION[c.source]),
                         key=lambda c: -c.traction)
    if established:
        composite -= 0.08 + 0.04 * min(len(established) - 1, 2)

    breaker_odds = {k: float(answers[f"deal_{k}"]["noul"]) for k in DEALBREAKERS}
    breakers = [DEALBREAKER_LABELS[k] for k, p in breaker_odds.items() if p >= DEALBREAKER_AT]
    margins = [(p - DEALBREAKER_AT) / (1 - DEALBREAKER_AT) for p in breaker_odds.values() if p >= DEALBREAKER_AT]
    for key, (floor, label) in FLOORS.items():
        position = dims[key]["value"] * (len(SCORES[key]["criteria"]) - 1)
        if position < floor:
            breakers.append(label)
            margins.append((floor - position) / floor)

    verdict: Literal["BUILD", "MAYBE", "DON'T BUILD"]
    if breakers:
        verdict = "DON'T BUILD"
        margin = max(margins)
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
    confidence = max(0.05, min(0.95, 0.5 + 0.45 * min(max(margin, 0.0), 1.0) * (0.5 + 0.5 * jev_certainty)))

    ranked = sorted(dims.values(), key=lambda d: d["value"])
    against = [f"{d['label']}: {d['level']}" for d in ranked if d["value"] < 0.45][:3]
    for_ = [f"{d['label']}: {d['level']}" for d in reversed(ranked) if d["value"] >= 0.6][:3]
    if established:
        top = established[0]
        against.insert(0, f"Already exists: {top.title} ({top.traction} {top.unit})")
    overlaps = sorted((c for c in candidates if (c.overlap or 0) >= 0.5), key=lambda c: -(c.overlap or 0))
    return Verdict(verdict, confidence, composite, for_, against, breakers, overlaps, dims)


# ---------------------------------------------------------------- output


def report_text(idea: str, v: Verdict, notes: list[str], searched: int, tokens: int) -> str:
    lines = [f"\n  {v.verdict}  ({v.confidence:.0%} confidence)\n", f"  {idea}\n"]
    for title, mark, items in (("Deal-breakers", "x", v.dealbreakers), ("For", "+", v.reasons_for),
                               ("Against", "-", v.reasons_against)):
        if items:
            lines.append(f"  {title}:")
            lines += [f"    {mark} {r}" for r in items]
    lines.append(f"\n  Prior art ({searched} checked):")
    if v.overlaps:
        lines += [f"    {c.overlap:.0%} overlap  {c.title} ({c.traction} {c.unit})  {c.url}" for c in v.overlaps[:5]]
    else:
        lines.append("    nothing found that does the same job")
    lines += [f"  note: {n}" for n in notes]
    lines.append(f"\n  {tokens} input tokens. Confidence isn't calibrated against real outcomes yet.\n")
    return "\n".join(lines)


def report_markdown(idea: str, v: Verdict, notes: list[str], searched: int) -> str:
    """Short enough for a Discord message; links wrapped in <> so they don't unfurl."""
    lines = [f"**{v.verdict}** ({v.confidence:.0%} confidence)", f"> {idea[:300]}"]
    for title, items in (("Deal-breakers", v.dealbreakers), ("For", v.reasons_for), ("Against", v.reasons_against)):
        if items:
            lines.append(f"**{title}**")
            lines += [f"- {r}" for r in items]
    if v.overlaps:
        lines.append(f"**Prior art** ({searched} checked)")
        lines += [f"- {c.overlap:.0%} overlap: [{c.title}](<{c.url}>) ({c.traction} {c.unit})" for c in v.overlaps[:3]]
    else:
        lines.append(f"**Prior art**: nothing found that does the same job ({searched} checked)")
    lines += [f"-# {n}" for n in notes]
    lines.append("-# buildornot · confidence isn't calibrated against real outcomes yet")
    return "\n".join(lines)


# ---------------------------------------------------------------- main


def run(idea: str, for_whom: str = "", builder: str = "", search: str | None = None) -> tuple[Verdict, Json]:
    notes: list[str] = []
    idea = " ".join(idea.split())
    if len(idea) < 15:
        raise BuildOrNotError("Describe the idea in at least a full sentence: what it does and the problem it solves.")
    if len(idea) > MAX_IDEA_CHARS:
        idea = idea[:MAX_IDEA_CHARS]
        notes.append(f"idea cut to its first {MAX_IDEA_CHARS} characters")
    queries = [search] if search else pick_search_terms(idea, notes)
    candidates = find_prior_art(queries, notes)
    state, questions = build_request(idea, for_whom, builder, candidates)
    response = ask_jev(state, questions)
    try:
        verdict = decide(response["answers"], candidates)
    except (KeyError, TypeError, ValueError) as exc:
        raise BuildOrNotError(f"Jev's response was missing an expected answer ({exc}).") from exc
    record: Json = {
        "version": __version__, "model": response.get("model"), "queries": queries, "notes": notes,
        "request": {"state": state, "questions": questions}, "response": response,
        "verdict": verdict.verdict, "confidence": verdict.confidence, "composite": verdict.composite,
        "outcome": None,  # fill in later (shipped? used? abandoned?) to calibrate
    }
    return verdict, record


def save_run(record: Json) -> Path | None:
    run_id = hashlib.sha256(json.dumps(record["request"], sort_keys=True).encode()).hexdigest()[:12]
    try:
        runs = HOME / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        path = runs / f"{run_id}.json"
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None  # saving is a convenience; never fail the verdict over it


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="buildornot", description="Should you build this side project?")
    parser.add_argument("idea", help="the idea, in a sentence or two")
    parser.add_argument("--for", dest="for_whom", default="", help="who it's for and the use case")
    parser.add_argument("--builder", default="", help="your skills, time and any audience you already have")
    parser.add_argument("--search", default=None, help="your own prior-art search terms")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--md", action="store_true", help="print a short Markdown report, ready for Discord")
    out.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--no-save", action="store_true", help=f"don't keep a copy in {HOME / 'runs'}")
    parser.add_argument("--version", action="version", version=f"buildornot {__version__}")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):  # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        verdict, record = run(args.idea, args.for_whom, args.builder, args.search)
    except BuildOrNotError as exc:
        print(f"buildornot: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    if not args.no_save:
        save_run(record)

    searched = len(record["request"]["state"]["prior_art"]) if isinstance(
        record["request"]["state"]["prior_art"], list) else 0
    if args.json:
        print(json.dumps(record, indent=2))
    elif args.md:
        print(report_markdown(record["request"]["state"]["idea"], verdict, record["notes"], searched))
    else:
        tokens = int(record["response"].get("usage", {}).get("input_tokens", 0))
        print(report_text(record["request"]["state"]["idea"], verdict, record["notes"], searched, tokens))
    return 0


if __name__ == "__main__":
    sys.exit(main())
