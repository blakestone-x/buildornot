# buildornot

Should you build this side project? One command gives you **BUILD**, **MAYBE** or **DON'T BUILD**, the reasons, and the prior art it found.

```
$ buildornot "A command line tool that converts Markdown files to PDF" --for "developers writing docs" --no-save

  DON'T BUILD  (leaning)
  score 0.31; BUILD at 0.60 or more, DON'T BUILD at 0.42 or less

  A command line tool that converts Markdown files to PDF
  for: developers writing docs

  For:
    + Feasibility (5/5): Fits that budget easily, with room to spare
  Against:
    - Already exists: alanshaw/markdown-pdf (2880 stars)
    - Pain (2/5): A minor irritation with almost no cost
    - Reach (1/5): No way to reach them is apparent

  Searched GitHub and Hacker News for: "markdown pdf", "markdown"
    89% likely same job  alanshaw/markdown-pdf (2880 stars)  https://github.com/alanshaw/markdown-pdf
  note: no --builder given: assumed one experienced developer, evenings for a month, no audience
```

## Try it

Python 3.10+, no dependencies. You need a [TypeSafe](https://typesafe.ai) API key.

```bash
export TYPESAFE_API_KEY=your-key       # PowerShell: $env:TYPESAFE_API_KEY = "your-key"
uvx --from git+https://github.com/blakestone-x/buildornot buildornot "your idea"
```

To keep it around, `pipx install git+https://github.com/blakestone-x/buildornot`, or clone the repo and run `python buildornot.py`. Instead of the environment variable you can put the line `TYPESAFE_API_KEY=your-key` in `~/.buildornot/.env` or in a `.env` file in the folder you run it from (see `.env.example`).

## Use

```bash
buildornot "your idea, in a sentence or two" --for "who it's for" --builder "your skills, time, audience"
```

A verdict on a one-line idea with no context is mostly a verdict on the wording. `--for` and `--builder` are optional, but they drive half the judgments, and the output says what it assumed when you leave them out.

| Option | What it does |
| --- | --- |
| `--for` | Who it's for and the use case. |
| `--builder` | Your skills, time and any audience you already have. |
| `--md` | Short Markdown report, ready to paste into Discord (links won't unfurl). |
| `--json` | Everything, including every raw Jev answer. |
| `--search` | Your own prior-art search terms, when the automatic ones miss. |
| `--no-save` | Don't save the run. |

Each run is saved as a new file in `~/.buildornot/runs/` (set `BUILDORNOT_HOME` to move it), including your idea text. A run costs a few thousand Jev input tokens, well under a cent. Each run makes up to two GitHub searches, and unauthenticated GitHub search allows 10 a minute, so set `GITHUB_TOKEN` if you run it a lot. Only public repositories are searched either way.

## Reading the verdict

The first line is the verdict and how strong it is. BUILD and DON'T BUILD are **clear**, **leaning** or **close call**. A MAYBE says which way it **leans** (build or don't build), or that it's **held back** from BUILD by one of the reasons below. The second line is the score behind it and the lines it's measured against (BUILD at 0.60 or more, DON'T BUILD at 0.42 or less), or the deal-breaker that stopped it.

- **Deal-breakers** end it regardless of the score: solution looking for a problem, depends on access you can't get, breaks terms or privacy, useless until it has lots of users, almost no real problem, or far beyond what you can build.
- **Holding it back from BUILD** lists what keeps a good score at MAYBE: a deal-breaker that's possible but not likely, existing tools that already cover it with no reason given to build anyway, or prior art that couldn't be fully checked (including when GitHub's rate limit stops its search).
- **For** and **Against** show the judgments that pushed each way, with the level Jev picked out of five.
- **Prior art** lists matches with how likely Jev thinks each one does the same job.

The weights and thresholds are hand-set. They aren't yet fit to real outcomes, so treat "clear" as "clear under these rules", not as a prediction.

## How it works

1. **Pick search terms.** Code lists the words and word pairs in your idea; Jev rates which ones would find projects doing the same job; code searches with the best two.
2. **Find prior art** on GitHub (public repo names and descriptions) and Hacker News.
3. **Ask Jev.** One request with narrow questions answered in parallel: six 5-level Scores (pain, frequency, audience, already solved, feasibility, reach), the deal-breaker checks, whether you gave a reason to build despite existing tools, and one same-job check per prior-art result. Text from your idea and from search results is passed as material to judge, never as instructions.
4. **Code makes the call** from those answers. Every weight and threshold sits near the top of `buildornot.py`.

## Calibration

Every saved run has an empty `outcome` field. The plan: once there are enough ideas with known outcomes (Show HN posts and their traction, repos still active a year later), fit the weights to what actually happened. TypeSafe's [autoresearch feature discovery](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery) cookbook shows the pattern.

## Development

```bash
python -m unittest discover -s tests   # offline, no key needed
python evals/run.py                    # 24 hand-labeled ideas against live Jev (~80k tokens)
```

The cases in `evals/ideas.json` are judgment calls, not outcomes: each lists the verdicts that would be reasonable. They include a prompt-injection attempt and a description of this tool itself. They catch a change that makes obviously bad calls; they don't prove calibration.

## Limits

- Search sees GitHub and Hacker News only, not app stores, Product Hunt or closed-source products. Jev's "already solved" judgment also draws on general knowledge.
- It judges the idea as written. A vague description gets a vague verdict.
- It's for side projects. Startup questions like market size and willingness to pay aren't asked.

## License

MIT
