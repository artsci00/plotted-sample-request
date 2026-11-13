import copy
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import refresh_sample_dates as r


def title(text):
    return [{"plain_text": text}]


def page(pid, name, parent_db, **dates):
    props = {"Name": {"type": "title", "title": title(name)}}
    for k, v in dates.items():
        k = k.replace("_", " ")
        if v is None:
            props[k] = {"type": "date", "date": None}
        elif isinstance(v, dict):
            props[k] = {"type": "date", "date": v}
        else:
            props[k] = {"type": "date", "date": {"start": v, "end": None, "time_zone": None}}
    return {"object": "page", "id": pid, "parent": {"type": "database_id", "database_id": parent_db},
            "properties": props}


def database(did, name, parent):
    return {"object": "database", "id": did, "title": title(name), "parent": parent,
            "properties": {"Name": {"type": "title"}}}


class FakeNotion:
    def __init__(self, dbs, pages, blocks=None, fail_on=()):
        self.dbs = {d["id"]: d for d in dbs}
        self.pages = {p["id"]: p for p in pages}
        self.blocks = blocks or {}
        self.fail_on = set(fail_on)
        self.patches = []

    def request(self, method, path, body=None):
        if path == "/search":
            q = r.norm(body["query"])
            return {"results": [d for d in self.dbs.values() if q in r.norm(r.db_title(d))],
                    "has_more": False}
        if path.startswith("/databases/") and path.endswith("/query"):
            did = path.split("/")[2]
            rows = [p for p in self.pages.values() if p["parent"]["database_id"] == did]
            f = body.get("filter")
            if f:
                rows = [p for p in rows if r.page_title(p) == f["title"]["equals"]]
            return {"results": copy.deepcopy(rows), "has_more": False}
        if path.startswith("/databases/"):
            return self.dbs[path.split("/")[2]]
        if path.startswith("/blocks/"):
            return self.blocks[path.split("/")[2]]
        if method == "PATCH" and path.startswith("/pages/"):
            pid = path.split("/")[2]
            if pid in self.fail_on:
                raise RuntimeError("PATCH failed (simulated)")
            self.patches.append(pid)
            for name, val in body["properties"].items():
                self.pages[pid]["properties"][name]["date"] = val["date"]
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def date_of(self, pid, prop):
        d = self.pages[pid]["properties"][prop]["date"]
        return d and d["start"]


TPL = {"name": "Perf", "ref": {"db": "Feedback Log", "row": "Caught it", "prop": "Date", "offset": -4},
       "targets": ["Feedback Log"]}


def perf_world(**kw):
    dbs = [database("db1", "Feedback Log", {"type": "page_id", "page_id": "P1"})]
    pages = [
        page("ref", "Caught it", "db1", Date="2026-08-28"),
        page("a", "Old note", "db1", Date="2026-06-19", Published="2025-01-01"),
        page("b", "Ranged", "db1", Date={"start": "2026-08-17", "end": "2026-09-11", "time_zone": None}),
        page("c", "Blank", "db1", Date=None),
    ]
    return FakeNotion(dbs, pages, **kw)


class ShiftValueTests(unittest.TestCase):
    def test_date_only_stays_date_only(self):
        self.assertEqual(r.shift_value("2026-08-28", 73), "2026-11-09")

    def test_keeps_local_clock_time_across_daylight_saving(self):
        self.assertEqual(r.shift_value("2026-08-25T17:00:00.000-07:00", 94),
                         "2026-11-27T17:00:00.000-08:00")

    def test_utc_z_is_read_as_utc(self):
        # 00:30 UTC Aug 26 = 17:30 Pacific Aug 25
        self.assertEqual(r.shift_value("2026-08-26T00:30:00.000Z", 1), "2026-08-26T17:30:00.000-07:00")

    def test_naive_time_with_explicit_zone_is_shifted_as_is(self):
        self.assertEqual(r.shift_value("2026-09-24T09:00:00.000", 7), "2026-10-01T09:00:00.000")

    def test_local_date_of_datetime_uses_pacific(self):
        self.assertEqual(r.local_date("2026-08-26T00:30:00.000Z"), date(2026, 8, 25))


class RunTests(unittest.TestCase):
    def run_tpl(self, world, today="2026-11-13", dry=False):
        logs = []
        res = r.run_template(world, TPL, date.fromisoformat(today), dry, logs.append)
        return res, logs

    def test_shifts_every_date_but_published_and_blanks(self):
        w = perf_world()
        (written, failed), _ = self.run_tpl(w)
        self.assertEqual((written, failed), (3, 0))
        self.assertEqual(w.date_of("ref", "Date"), "2026-11-09")        # today - 4
        self.assertEqual(w.date_of("a", "Date"), "2026-08-31")          # +73
        self.assertEqual(w.date_of("a", "Published"), "2025-01-01")     # untouched
        rng = w.pages["b"]["properties"]["Date"]["date"]
        self.assertEqual((rng["start"], rng["end"]), ("2026-10-29", "2026-11-23"))
        self.assertIsNone(w.pages["c"]["properties"]["Date"]["date"])

    def test_second_run_same_day_changes_nothing(self):
        w = perf_world()
        self.run_tpl(w)
        before = len(w.patches)
        (written, _), logs = self.run_tpl(w)
        self.assertEqual(written, 0)
        self.assertEqual(len(w.patches), before)
        self.assertIn("already current", logs[0])

    def test_missed_runs_catch_up_in_one_go(self):
        w = perf_world()
        self.run_tpl(w, today="2026-11-13")
        self.run_tpl(w, today="2026-11-30")     # 17 days later, no runs between
        self.assertEqual(w.date_of("ref", "Date"), "2026-11-26")
        self.assertEqual(w.date_of("a", "Date"), "2026-09-17")

    def test_dry_run_writes_nothing(self):
        w = perf_world()
        (written, _), logs = self.run_tpl(w, dry=True)
        self.assertEqual(written, 3)
        self.assertEqual(w.patches, [])
        self.assertTrue(any("would set" in line for line in logs))

    def test_reference_row_is_written_first(self):
        w = perf_world()
        self.run_tpl(w)
        self.assertEqual(w.patches[0], "ref")

    def test_reference_failure_moves_nothing(self):
        w = perf_world(fail_on={"ref"})
        with self.assertRaises(r.TemplateError):
            self.run_tpl(w)
        self.assertEqual(w.patches, [])
        self.assertEqual(w.date_of("a", "Date"), "2026-06-19")

    def test_other_row_failure_is_reported_not_fatal(self):
        w = perf_world(fail_on={"a"})
        (written, failed), _ = self.run_tpl(w)
        self.assertEqual((written, failed), (2, 1))

    def test_implausible_shift_is_refused(self):
        w = perf_world()
        with self.assertRaises(r.TemplateError):
            self.run_tpl(w, today="2028-06-01")
        self.assertEqual(w.patches, [])


class AmbiguityTests(unittest.TestCase):
    def test_same_named_database_in_another_template_is_left_alone(self):
        # Interview Loop and Competitor Signal Tracker both have a "Signals" database.
        dbs = [
            database("comp", "Signals", {"type": "page_id", "page_id": "COMPETITOR"}),
            database("hire", "Signals", {"type": "page_id", "page_id": "HIRING"}),
        ]
        pages = [
            page("g", "Gridline raises $18M Series A", "comp", Captured="2026-09-09"),
            page("h", "Debugs systems", "hire", Captured="2026-01-01"),
        ]
        w = FakeNotion(dbs, pages)
        tpl = {"name": "Comp", "ref": {"db": "Signals", "row": "Gridline raises $18M Series A",
                                       "prop": "Captured", "offset": -12}, "targets": ["Signals"]}
        r.run_template(w, tpl, date(2026, 11, 13), False, lambda *_: None)
        self.assertEqual(w.date_of("g", "Captured"), "2026-11-01")
        self.assertEqual(w.date_of("h", "Captured"), "2026-01-01")

    def test_duplicate_reference_row_stops_the_template(self):
        dbs = [database("x", "Feedback Log", {"type": "page_id", "page_id": "P1"}),
               database("y", "Feedback Log", {"type": "page_id", "page_id": "P1 copy"})]
        pages = [page("1", "Caught it", "x", Date="2026-08-28"),
                 page("2", "Caught it", "y", Date="2026-08-28")]
        w = FakeNotion(dbs, pages)
        with self.assertRaises(r.TemplateError):
            r.run_template(w, TPL, date(2026, 11, 13), False, lambda *_: None)
        self.assertEqual(w.patches, [])

    def test_database_inside_a_column_resolves_to_its_template_page(self):
        dbs = [database("db1", "Feedback Log", {"type": "block_id", "block_id": "col"})]
        blocks = {"col": {"parent": {"type": "block_id", "block_id": "colset"}},
                  "colset": {"parent": {"type": "page_id", "page_id": "P1"}}}
        w = FakeNotion(dbs, [page("ref", "Caught it", "db1", Date="2026-08-28")], blocks=blocks)
        r.run_template(w, TPL, date(2026, 11, 13), False, lambda *_: None)
        self.assertEqual(w.date_of("ref", "Date"), "2026-11-09")

    def test_emoji_in_database_title_still_matches(self):
        self.assertEqual(r.norm("📋 Assignments & Tasks"), r.norm("Assignments & Tasks"))
        self.assertNotEqual(r.norm("Assignments & Tasks"), r.norm("Assignments"))


class MainTests(unittest.TestCase):
    def test_one_template_failing_does_not_stop_the_others(self):
        dbs = [database("db1", "Feedback Log", {"type": "page_id", "page_id": "P1"})]
        pages = [page("ref", "Caught the race condition before it shipped", "db1", Date="2026-08-28"),
                 page("a", "Old note", "db1", Date="2026-06-19")]
        w = FakeNotion(dbs, pages)
        lines = []
        code = r.main(["--today", "2026-11-13"], client=w, out=lines.append)
        self.assertEqual(code, 1)                              # the other eight aren't in this fake
        self.assertEqual(w.date_of("ref", "Date"), "2026-11-09")  # Performance & Feedback still ran
        self.assertEqual(w.date_of("a", "Date"), "2026-08-31")
        self.assertEqual(sum("ERROR" in line for line in lines), 8)

    def test_only_runs_one_template(self):
        w = FakeNotion([], [])
        lines = []
        r.main(["--only", "executive os", "--dry-run"], client=w, out=lines.append)
        self.assertEqual(sum(line.startswith("\n") for line in lines if "Executive OS" in line), 1)


if __name__ == "__main__":
    unittest.main()
