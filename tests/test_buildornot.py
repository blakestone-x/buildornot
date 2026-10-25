"""Offline tests: no network, no API key. Run with `python -m unittest`."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buildornot as bon  # noqa: E402


def answers(scores: dict[str, float] | None = None, deals: dict[str, float] | None = None,
            overlaps: list[float] | None = None, confidence: float = 0.8) -> dict[str, Any]:
    """Jev-shaped answers. Scores are 0-4 positions; `solved` is high when already solved."""
    base = {"pain": 3.0, "frequency": 3.0, "audience": 3.0, "solved": 1.0, "feasible": 3.0, "reach": 3.0}
    base.update(scores or {})
    out: dict[str, Any] = {k: {"type": "score", "score": v, "confidence": confidence} for k, v in base.items()}
    for k in bon.DEALBREAKERS:
        out[f"deal_{k}"] = {"type": "noul", "noul": (deals or {}).get(k, 0.05)}
    for n, p in enumerate(overlaps or []):
        out[f"overlap_{n}"] = {"type": "noul", "noul": p}
    return out


def repo(stars: int, name: str = "someone/tool") -> bon.Candidate:
    return bon.Candidate("github", name, f"https://github.com/{name}", "does the thing", stars)


class DecideTests(unittest.TestCase):
    def test_strong_idea_builds(self) -> None:
        v = bon.decide(answers({"pain": 4, "frequency": 4, "feasible": 4, "reach": 4, "audience": 4, "solved": 0}), [])
        self.assertEqual(v.verdict, "BUILD")
        self.assertTrue(v.reasons_for)

    def test_weak_idea_does_not_build(self) -> None:
        v = bon.decide(answers({"pain": 1, "frequency": 0, "audience": 0, "solved": 4, "reach": 0}), [])
        self.assertEqual(v.verdict, "DON'T BUILD")
        self.assertFalse(v.dealbreakers)

    def test_dealbreaker_overrides_good_scores(self) -> None:
        v = bon.decide(answers({"pain": 4, "frequency": 4, "solved": 0}, deals={"needs_crowd": 0.9}), [])
        self.assertEqual(v.verdict, "DON'T BUILD")
        self.assertEqual(v.dealbreakers, [bon.DEALBREAKER_LABELS["needs_crowd"]])

    def test_no_problem_cannot_be_outweighed(self) -> None:
        v = bon.decide(answers({"pain": 0.1, "audience": 4, "reach": 4, "feasible": 4, "frequency": 4, "solved": 0}), [])
        self.assertEqual(v.verdict, "DON'T BUILD")
        self.assertIn("No real problem described", v.dealbreakers)

    def test_out_of_reach_cannot_be_outweighed(self) -> None:
        v = bon.decide(answers({"feasible": 0.2, "pain": 4, "frequency": 4, "solved": 0}), [])
        self.assertEqual(v.verdict, "DON'T BUILD")
        self.assertIn("Far beyond what you can build", v.dealbreakers)

    def test_dealbreaker_below_threshold_is_ignored(self) -> None:
        v = bon.decide(answers(deals={"no_problem": bon.DEALBREAKER_AT - 0.01}), [])
        self.assertFalse(v.dealbreakers)

    def test_established_overlap_lowers_score_and_leads_reasons(self) -> None:
        clean = bon.decide(answers(), [])
        hit = bon.decide(answers(overlaps=[0.9]), [repo(5000, "big/tool")])
        self.assertLess(hit.composite, clean.composite)
        self.assertTrue(hit.reasons_against[0].startswith("Already exists: big/tool (5000 stars)"))

    def test_overlap_without_traction_is_not_penalized(self) -> None:
        clean = bon.decide(answers(), [])
        small = bon.decide(answers(overlaps=[0.95]), [repo(3)])
        self.assertAlmostEqual(small.composite, clean.composite)
        self.assertEqual(len(small.overlaps), 1)

    def test_already_solved_counts_against_building(self) -> None:
        open_field = bon.decide(answers({"solved": 0}), [])
        crowded = bon.decide(answers({"solved": 4}), [])
        self.assertGreater(open_field.composite, crowded.composite)
        self.assertTrue(any(r.startswith("How well it's already solved") for r in crowded.reasons_against))

    def test_out_of_range_scores_are_clamped(self) -> None:
        v = bon.decide(answers({"pain": 9, "solved": -2}), [])
        self.assertLessEqual(v.dimensions["pain"]["value"], 1.0)
        self.assertLessEqual(v.dimensions["solved"]["value"], 1.0)

    def test_confidence_stays_in_bounds(self) -> None:
        for conf in (0.0, 1.0):
            for scores in ({"pain": 4, "frequency": 4, "solved": 0}, {"pain": 0, "solved": 4}):
                v = bon.decide(answers(scores, confidence=conf), [])
                self.assertGreaterEqual(v.confidence, 0.05)
                self.assertLessEqual(v.confidence, 0.95)

    def test_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(bon.WEIGHTS.values()), 1.0)
        self.assertEqual(set(bon.WEIGHTS), set(bon.SCORES))


class SearchTermTests(unittest.TestCase):
    def test_candidates_drop_stopwords_and_keep_pairs(self) -> None:
        terms = bon.candidate_terms("A command line tool that converts Markdown files to PDF documents")
        self.assertIn("markdown", terms)
        self.assertIn("pdf documents", terms)
        self.assertNotIn("command", terms)
        self.assertNotIn("tool", terms)

    def test_picks_two_strongest_terms_without_repeating_a_word(self) -> None:
        idea = "Convert markdown notes into printable PDF booklets"
        terms = bon.candidate_terms(idea)
        odds = {t: 0.1 for t in terms}
        odds.update({"markdown": 0.9, "markdown notes": 0.85, "pdf": 0.8})
        fake = {"answers": {f"term_{n}": {"noul": odds[t]} for n, t in enumerate(terms)}}
        with mock.patch.object(bon, "ask_jev", return_value=fake):
            queries, raw = bon.pick_search_terms(idea, [])
        self.assertEqual(queries, ["markdown pdf", "markdown"])
        self.assertIs(raw, fake)

    def test_falls_back_to_keywords_when_jev_fails(self) -> None:
        notes: list[str] = []
        with mock.patch.object(bon, "ask_jev", side_effect=bon.BuildOrNotError("down")):
            queries, raw = bon.pick_search_terms("Convert markdown notes into printable PDF booklets", notes)
        self.assertEqual(len(queries), 1)
        self.assertIsNone(raw)
        self.assertIn("plain keywords", notes[0])


class PriorArtTests(unittest.TestCase):
    def test_failed_search_becomes_a_note(self) -> None:
        notes: list[str] = []
        with mock.patch.object(bon, "search_github", side_effect=bon.HttpError(403, "rate limited")), \
                mock.patch.object(bon, "search_hn", return_value=[repo(1, "x/y")]):
            found = bon.find_prior_art(["markdown pdf"], notes)
        self.assertEqual(len(found), 1)
        self.assertIn("GITHUB_TOKEN", notes[0])

    def test_duplicates_are_merged_and_capped(self) -> None:
        many = [repo(i, f"o/r{i}") for i in range(10)]
        with mock.patch.object(bon, "search_github", return_value=many), \
                mock.patch.object(bon, "search_hn", return_value=many[:4]):
            found = bon.find_prior_art(["a", "b"], [])
        self.assertEqual(len(found), 10)


class RunTests(unittest.TestCase):
    IDEA = "Replay saved Jev decisions for a UI test agent and only call Jev when the screen changed"

    def test_run_end_to_end_with_fakes(self) -> None:
        calls: list[dict[str, Any]] = []

        def fake_jev(state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
            calls.append(questions)
            if "pain" in questions:
                n = sum(1 for q in questions if q.startswith("overlap_"))
                return {"model": "jev-test", "answers": answers(overlaps=[0.2] * n), "usage": {"input_tokens": 10}}
            return {"answers": {q: {"noul": 0.5} for q in questions}}

        with mock.patch.object(bon, "ask_jev", side_effect=fake_jev), \
                mock.patch.object(bon, "search_github", return_value=[repo(10)]), \
                mock.patch.object(bon, "search_hn", return_value=[]):
            verdict, record = bon.run(self.IDEA, "QA engineers")
        self.assertEqual(len(calls), 2)  # search terms, then the judgment
        self.assertIn(verdict.verdict, ("BUILD", "MAYBE", "DON'T BUILD"))
        self.assertIsNone(record["outcome"])
        self.assertEqual(record["request"]["state"]["for_whom"], "QA engineers")

    def test_explicit_search_skips_the_term_request(self) -> None:
        with mock.patch.object(bon, "ask_jev", return_value={"answers": answers()}) as jev, \
                mock.patch.object(bon, "search_github", return_value=[]), \
                mock.patch.object(bon, "search_hn", return_value=[]):
            _, record = bon.run(self.IDEA, search="replay cache")
        self.assertEqual(jev.call_count, 1)
        self.assertEqual(record["queries"], ["replay cache"])
        self.assertEqual(record["request"]["state"]["prior_art"], "no prior art was found by search")

    def test_too_short_idea_is_rejected(self) -> None:
        with self.assertRaises(bon.BuildOrNotError):
            bon.run("an app")

    def test_long_idea_is_cut_with_a_note(self) -> None:
        with mock.patch.object(bon, "ask_jev", return_value={"answers": answers()}), \
                mock.patch.object(bon, "find_prior_art", return_value=[]):
            _, record = bon.run("x" * 20 + " word" * 1000, search="x")
        self.assertEqual(len(record["request"]["state"]["idea"]), bon.MAX_IDEA_CHARS)
        self.assertTrue(any("cut" in n for n in record["notes"]))

    def test_missing_answer_is_a_clean_error(self) -> None:
        with mock.patch.object(bon, "ask_jev", return_value={"answers": {}}), \
                mock.patch.object(bon, "find_prior_art", return_value=[]):
            with self.assertRaises(bon.BuildOrNotError):
                bon.run(self.IDEA, search="x")


class OutputTests(unittest.TestCase):
    def test_markdown_fits_a_discord_message_and_hides_previews(self) -> None:
        v = bon.decide(answers(overlaps=[0.9, 0.8, 0.7]), [repo(900, f"o/r{i}") for i in range(3)])
        md = bon.report_markdown("idea " * 100, v, ["a note"], 12)
        self.assertLess(len(md), 2000)
        self.assertIn("(<https://github.com/o/r0>)", md)

    def test_main_prints_errors_and_exits_1(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = bon.main(["tiny"])
        self.assertEqual(code, 1)
        self.assertIn("buildornot:", err.getvalue())


class KeyTests(unittest.TestCase):
    def test_reads_key_from_home_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TYPESAFE_API_KEY", None)
            Path(home, ".env").write_text("TYPESAFE_API_KEY='abc123'\n", encoding="utf-8")
            with mock.patch.object(bon, "HOME", Path(home)), mock.patch.object(Path, "cwd", return_value=Path(home) / "x"):
                self.assertEqual(bon.api_key(), "abc123")

    def test_missing_key_explains_where_to_put_it(self) -> None:
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TYPESAFE_API_KEY", None)
            with mock.patch.object(bon, "HOME", Path(home)), mock.patch.object(Path, "cwd", return_value=Path(home)):
                with self.assertRaisesRegex(bon.BuildOrNotError, "TYPESAFE_API_KEY"):
                    bon.api_key()


if __name__ == "__main__":
    unittest.main()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ReviewFixTests(unittest.TestCase):
    """Regressions for the pre-launch review findings."""

    def setUp(self) -> None:
        sleep = mock.patch.object(bon.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_non_json_body_is_an_http_error(self) -> None:
        with mock.patch.object(bon.urllib.request, "urlopen", return_value=FakeResponse(b"<html>502</html>")):
            with self.assertRaisesRegex(bon.HttpError, "wasn't JSON"):
                bon.request_json("https://example.test/x")

    def test_dropped_connection_is_retried_then_an_http_error(self) -> None:
        import http.client
        with mock.patch.object(bon.urllib.request, "urlopen",
                               side_effect=http.client.RemoteDisconnected("closed")) as opened:
            with self.assertRaises(bon.HttpError):
                bon.request_json("https://example.test/x", retries=2)
        self.assertEqual(opened.call_count, 3)

    def test_connection_reset_during_search_is_a_note_not_a_crash(self) -> None:
        notes: list[str] = []
        with mock.patch.object(bon.urllib.request, "urlopen", side_effect=ConnectionResetError("reset")):
            found = bon.find_prior_art(["markdown pdf"], notes)
        self.assertEqual(found, [])
        self.assertEqual(len(notes), 2)  # GitHub and Hacker News both noted

    def test_auth_header_is_kept_off_redirects(self) -> None:
        seen: list[Any] = []

        def capture(req: Any, timeout: float) -> FakeResponse:
            seen.append(req)
            return FakeResponse(b'{"answers": {}}')

        with mock.patch.object(bon.urllib.request, "urlopen", side_effect=capture):
            bon.request_json("https://example.test/x", body={"a": 1}, auth="Bearer secret")
        req = seen[0]
        self.assertEqual(req.unredirected_hdrs.get("Authorization"), "Bearer secret")
        self.assertNotIn("Authorization", req.headers)

    def test_jev_response_without_answers_is_a_clean_error(self) -> None:
        with mock.patch.object(bon, "request_json", return_value={"error": "overloaded"}), \
                mock.patch.object(bon, "api_key", return_value="k"):
            with self.assertRaisesRegex(bon.BuildOrNotError, "no answers"):
                bon.ask_jev({}, {})

    def test_partial_term_answers_fall_back_to_keywords(self) -> None:
        notes: list[str] = []
        with mock.patch.object(bon, "ask_jev", return_value={"answers": {"term_0": {"noul": None}}}):
            queries, raw = bon.pick_search_terms("Convert markdown notes into printable PDF booklets", notes)
        self.assertTrue(queries[0])
        self.assertIsNone(raw)
        self.assertIn("plain keywords", notes[0])

    def test_idea_of_common_words_still_gets_searched(self) -> None:
        idea = "An app that helps people track things they want to build"
        self.assertEqual(bon.candidate_terms(idea), [])
        queries, _ = bon.pick_search_terms(idea, [])
        self.assertTrue(queries and queries[0])

    def test_no_search_is_reported_honestly(self) -> None:
        with mock.patch.object(bon, "pick_search_terms", return_value=([], None)), \
                mock.patch.object(bon, "ask_jev", return_value={"answers": answers()}):
            _, record = bon.run("An idea whose words yield nothing searchable at all")
        self.assertEqual(record["request"]["state"]["prior_art"], "unknown: no search was run")
        self.assertTrue(any("wasn't checked" in n for n in record["notes"]))

    def test_non_ascii_ideas_have_words(self) -> None:
        self.assertIn("planificateur", bon.words_of("Un planificateur de repas pour étudiants"))
        self.assertIn("étudiants", bon.words_of("Un planificateur de repas pour étudiants"))

    def test_saving_twice_never_overwrites(self) -> None:
        record = {"request": {"state": {"idea": "same"}}, "outcome": None}
        with tempfile.TemporaryDirectory() as home, mock.patch.object(bon, "HOME", Path(home)):
            first = bon.save_run(record)
            assert first is not None
            first.write_text('{"outcome": "shipped"}', encoding="utf-8")
            second = bon.save_run(record)
            self.assertNotEqual(first, second)
            self.assertIn("shipped", first.read_text(encoding="utf-8"))
