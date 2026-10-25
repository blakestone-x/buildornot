# buildornot

Should you build this side project? One command gives you **BUILD**, **MAYBE** or **DON'T BUILD**, a confidence score, the reasons, and the prior art it found.

```
$ python buildornot.py "A command line tool that converts Markdown files to PDF" --for "developers writing docs"

  DON'T BUILD  (59% confidence)

  For:
    + Whether you can build it: Clearly doable; the hard parts are known techniques
  Against:
    - Already exists: alanshaw/markdown-pdf (2880 stars)
    - Whether you can reach the people it's for: No channel is apparent
    - How well it's already solved: Popular, mature tools already do exactly this

  Prior art (10 checked):
    88% overlap  alanshaw/markdown-pdf (2880 stars)  https://github.com/alanshaw/markdown-pdf
    80% overlap  Show HN: CLI to export Markdown to PDF using Jinja2 templates (8 points)
```

## Setup

Python 3.10+, standard library only. You need a [TypeSafe](https://typesafe.ai) API key.

```bash
cp .env.example .env   # then put your TYPESAFE_API_KEY in .env
python buildornot.py "your idea" --for "who it's for" --builder "your skills, time, audience"
```

`--for` and `--builder` are optional but make a big difference: "who it's for" drives the audience and reach judgments, and "builder" drives whether you can actually ship it. `--search "terms"` overrides the prior-art search when the automatic keywords miss. `--json` prints everything, including every raw Jev answer.

Set `GITHUB_TOKEN` if you run it often; unauthenticated GitHub search allows 10 requests a minute.

## How it works

1. **Evidence first.** Plain code searches GitHub (repo names and descriptions) and Hacker News for prior art. Jev only knows what it's given, so this step matters most.
2. **One Jev request, many narrow questions**, answered in parallel:
   - Six Scores on concrete levels: how much the problem hurts, how often people hit it, how clearly you can name who it's for, how well it's already solved, whether you can build it, and whether you can reach the people it's for.
   - Four deal-breaker Nouls: solution looking for a problem, depends on access you can't get, breaks terms or privacy, useless until it has lots of users.
   - One overlap Noul per prior-art result: does this already do the same job for the same users?
3. **Plain code makes the call.** A likely deal-breaker means DON'T BUILD. Otherwise a weighted score, minus a penalty when an established project already does the same job, lands on BUILD, MAYBE or DON'T BUILD. All weights and thresholds sit at the top of `buildornot.py`.

A run costs roughly 3,000 to 8,000 input tokens.

## What the confidence means

Right now, confidence says how far past the line the idea landed and how settled Jev's answers were. It is **not** yet the probability that the project works out: the weights are informed guesses, not fit to outcomes.

That's the next step. Every run is saved in `runs/` with an empty `outcome` field. With enough ideas whose outcomes are known (Show HN posts and their traction, repos still active a year later), the Jev answers become features for a small model, the weights get fit to what actually happened, and the confidence becomes calibrated. TypeSafe's [autoresearch feature discovery](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery) cookbook shows the pattern, including using the worst misses to propose better questions.

## Limits

- The keyword search is simple. If it misses obvious prior art, pass `--search`.
- Search sees GitHub and Hacker News only, not app stores, Product Hunt or closed-source products. Jev's "already solved" judgment can still draw on general knowledge.
- It judges the idea as written. A vague description gets a vague verdict, often with a deal-breaker for no clear problem.
- It's for side projects. Startup questions like market size and willingness to pay aren't asked.
