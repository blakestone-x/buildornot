# buildornot

Should you build this side project? One command gives you **BUILD**, **MAYBE** or **DON'T BUILD**, a confidence score, the reasons, and the prior art it found.

```
$ buildornot "A command line tool that converts Markdown files to PDF" --for "developers writing docs"

  DON'T BUILD  (59% confidence)

  For:
    + Whether you can build it: Clearly doable; the hard parts are known techniques
  Against:
    - Already exists: alanshaw/markdown-pdf (2880 stars)
    - Whether you can reach the people it's for: No channel is apparent
    - How well it's already solved: Popular, mature tools already do exactly this

  Prior art (12 checked):
    87% overlap  alanshaw/markdown-pdf (2880 stars)  https://github.com/alanshaw/markdown-pdf
    81% overlap  Show HN: CLI to export Markdown to PDF using Jinja2 templates (8 points)
```

## Install

Python 3.10+, no dependencies. You need a [TypeSafe](https://typesafe.ai) API key.

```bash
pipx install git+https://github.com/blakestone-x/buildornot
mkdir -p ~/.buildornot && echo "TYPESAFE_API_KEY=your-key" >> ~/.buildornot/.env
```

Or skip installing: clone the repo and run `python buildornot.py`. The key can also come from the `TYPESAFE_API_KEY` environment variable or a `.env` file in the current folder.

## Use

```bash
buildornot "your idea, in a sentence or two" --for "who it's for" --builder "your skills, time, audience"
```

| Option | What it does |
| --- | --- |
| `--for` | Who it's for and the use case. Drives the audience and reach judgments. |
| `--builder` | Your skills, time and any audience you already have. Drives whether you can ship it. |
| `--md` | Short Markdown report, ready to paste into Discord (links won't unfurl). |
| `--json` | Everything, including every raw Jev answer. |
| `--search` | Your own prior-art search terms, when the automatic ones miss. |
| `--no-save` | Don't keep a copy of the run. |

`--for` and `--builder` are optional, but a verdict on a one-line idea with no context is mostly a verdict on the wording. Set `GITHUB_TOKEN` if you run it a lot: unauthenticated GitHub search allows 10 requests a minute.

A run costs a few thousand Jev input tokens, well under a cent.

## How it works

1. **Pick search terms.** Code lists the words and word pairs in your idea; Jev rates which ones name what the project is. Code builds two searches from the best.
2. **Find prior art.** GitHub (repo names and descriptions) and Hacker News. Jev only knows what it's given, so this step matters most.
3. **One Jev request, many narrow questions**, answered in parallel:
   - Six Scores on concrete levels: how much the problem hurts, how often people hit it, how clearly you can name who it's for, how well it's already solved, whether you can build it, and whether you can reach the people it's for.
   - Four deal-breaker checks: solution looking for a problem, depends on access you can't get, breaks terms or privacy, useless until it has lots of users.
   - One overlap check per prior-art result: does this already do the same job for the same users?
4. **Code makes the call.** A likely deal-breaker, almost no pain, or a project far beyond the builder means DON'T BUILD: those can't be made up for elsewhere. Otherwise a weighted score, minus a penalty when an established project already does the same job, lands on BUILD, MAYBE or DON'T BUILD. Every weight and threshold sits near the top of `buildornot.py`.

## What the confidence means

Right now, confidence says how far past the line the idea landed and how settled Jev's answers were. It is **not** yet the probability that the project works out: the weights are informed guesses, not fit to outcomes.

Every run is saved in `~/.buildornot/runs/` with an empty `outcome` field. Once there are enough ideas with known outcomes (Show HN posts and their traction, repos still active a year later), the Jev answers become features for a small model, the weights get fit to what actually happened, and the confidence becomes calibrated. TypeSafe's [autoresearch feature discovery](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery) cookbook shows the pattern.

## Development

```bash
python -m unittest discover -s tests   # offline, no key needed
python evals/run.py                    # 20 hand-labeled ideas against live Jev (~70k tokens)
```

The eval cases in `evals/ideas.json` are judgment calls, not outcomes: each lists the verdicts that would be reasonable. They catch a change that makes obviously bad calls; they don't prove calibration.

## Limits

- Search sees GitHub and Hacker News only, not app stores, Product Hunt or closed-source products. Jev's "already solved" judgment can still draw on general knowledge.
- It judges the idea as written. A vague description gets a vague verdict.
- It's for side projects. Startup questions like market size and willingness to pay aren't asked.

## License

MIT
