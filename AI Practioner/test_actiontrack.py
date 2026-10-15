"""Tests for actiontrack. Run: python -m unittest -v"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import actiontrack as at

ROOT = Path(__file__).resolve().parent
NOTES = ROOT / "notes"
TODAY = "2026-10-15"


def hashes(d):
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(d).rglob("*")) if p.is_file()}


def run(*argv):
    """Run the CLI in-process; return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = at.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class TempCwd(unittest.TestCase):
    """Each test runs in a fresh temp dir with a copy of the real notes at ./notes."""

    def setUp(self):
        self._old = os.getcwd()
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(NOTES, self.tmp / "notes")
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def note(self, name, text):
        p = self.tmp / "fx" / name
        p.parent.mkdir(exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class Phase1Skeleton(unittest.TestCase):
    def test_usage_error_exits_1(self):
        self.assertEqual(run("bogus")[0], at.EXIT_USER)
        self.assertEqual(run("list", "--status", "bogus")[0], at.EXIT_USER)
        self.assertEqual(run("list", "--due-before", "15/10/2026")[0], at.EXIT_USER)

    def test_four_commands_exist(self):
        for cmd in ("ingest", "list", "done", "followup"):
            self.assertEqual(run(cmd, "--help")[0], 0, cmd)


D = at.dt.date


def entries(name):
    return at.parse_note((NOTES / name).read_text(encoding="utf-8"))["entries"]


def find(es, needle):
    hits = [e for e in es if needle in e["description"]]
    assert len(hits) == 1, (needle, hits)
    return hits[0]


class Phase2Parsing(unittest.TestCase):
    def test_file_checks(self):
        self.assertEqual(at.read_note(NOTES / "2026-09-09-notes-final-v2.md")[1], "EMPTY_FILE")
        self.assertEqual(at.read_note(NOTES / "scratch.md")[1], "UNDECODABLE")
        self.assertEqual(at.read_note(NOTES / "2026-09-19-offsite-draft.txt")[1], "NOT_MARKDOWN")
        text, code = at.read_note(NOTES / "untitled.md")
        self.assertIsNone(code)
        self.assertIn("Sam to chase", text)

    def test_headers(self):
        cases = {
            "2026-09-01-quote-portal-kickoff.md": D(2026, 9, 1),
            "2026-09-09-qa-standup.md": D(2026, 9, 9),               # 9 September 2026
            "2026-09-14-onboarding-flow-design.md": D(2026, 9, 14),  # Monday, 14 September 2026
            "2026-09-16-standup.md": D(2026, 9, 16),                 # YAML front matter
            "2026-10-03-sales-pipeline.md": None,                    # sometime last week
        }
        for name, want in cases.items():
            n = at.parse_note((NOTES / name).read_text(encoding="utf-8"))
            self.assertEqual(n["meeting_date"], want, name)
        n = at.parse_note((NOTES / "2026-09-16-standup.md").read_text(encoding="utf-8"))
        self.assertEqual(n["attendees"], ["Anita Desai", "Lena Fischer", "Meera Iyer"])
        self.assertIn("DATE_MISSING", at.parse_note((NOTES / "untitled.md").read_text())["warnings"])
        n = at.parse_note((NOTES / "2026-09-23-standup.md").read_text(encoding="utf-8"))
        self.assertEqual(n["attendees"], [])
        self.assertIn("ATTENDEES_MISSING", n["warnings"])

    def test_pseudo_tasks_ignored(self):
        es = entries("2026-09-21-incident-review.md")
        refunds = [e for e in es if "refund" in e["description"]]
        self.assertEqual(len(refunds), 1)
        self.assertIsNone(refunds[0]["due_raw"])  # the blockquote copy said "by Friday"
        es = entries("2026-10-09-security-review.md")
        self.assertEqual(len(es), 4)
        self.assertFalse(any("hardware tokens" in e["description"] or "remediate" in e["description"]
                             for e in es))

    def test_checkbox_variants(self):
        es = entries("2026-10-02-finance-sync.md")
        self.assertEqual(len(es), 4)
        self.assertTrue(find(es, "approve the Q4 cloud spend")["checked"])     # [X]
        self.assertEqual(find(es, "cut cloud costs")["due_raw"], "2026-10-09")  # * bullet
        self.assertEqual(find(es, "pipeline forecast")["owners_raw"], ["Meera"])  # + bullet

    def test_offsite_all_formats(self):
        es = entries("2026-09-30-q4-offsite.md")
        validate = find(es, "validate targets")                  # nested sub-item
        self.assertEqual(validate["due"], D(2026, 10, 5))
        row = find(es, "Refresh the brand kit")
        self.assertEqual((row["format"], row["owners_raw"], row["due"]), ("table", ["Anita"], D(2026, 10, 9)))
        act = find(es, "circulate the offsite summary")
        self.assertEqual((act["format"], act["owners_raw"], act["due"]), ("action", ["Karan"], D(2026, 10, 1)))
        self.assertEqual(len(es), 9)  # 4 morning (incl. nested) + 3 afternoon + table row + ACTION

    def test_owner_shapes(self):
        es = entries("2026-09-18-design-review.md")
        self.assertEqual(find(es, "audit colour contrast")["owners_raw"], ["anita"])
        self.assertEqual(find(es, "agree on the component API")["owners_raw"], ["Anita", "Rahul"])
        lena = find(es, "collect user feedback")
        self.assertEqual(lena["owners_raw"], ["Lena"])
        self.assertIn("(with Priya)", lena["description"])
        self.assertEqual(find(es, "design-system changelog")["owners_raw"], ["TBD"])
        es = entries("2026-09-08-quote-portal-weekly.md")
        quote = find(es, "revised quote")
        self.assertEqual((quote["owners_raw"], quote["description"]), (["Priya N."], "send revised quote to Acme"))

    def test_malformed_entries(self):
        n = at.parse_note("# Meeting: x\nDate: 2026-10-01\n\n- [ ]\n- [ ]   \n"
                          "| Owner | Action | Due |\n|---|---|---|\n| Sam |  | 2026-10-02 |\n"
                          "| Sam | Ship it | 2026-10-03 |\n")
        self.assertEqual([e["description"] for e in n["entries"]], ["Ship it"])
        self.assertEqual(n["warnings"].count("MALFORMED_ENTRY"), 3)

    def test_no_owner_prefix(self):
        n = at.parse_note("Date: 2026-10-01\n- [ ] Book the venue\n")
        self.assertEqual(n["entries"][0]["owners_raw"], [])


class Phase3Dates(unittest.TestCase):
    def test_rules(self):
        cases = [  # (phrase, meeting date, expected)
            ("Friday", D(2026, 9, 1), D(2026, 9, 4)),          # Tue → upcoming Fri
            ("Friday", D(2026, 10, 9), D(2026, 10, 9)),        # Fri meeting → same day
            ("Mon", D(2026, 9, 18), D(2026, 9, 21)),
            ("Wed", D(2026, 9, 8), D(2026, 9, 9)),
            ("next Tuesday", D(2026, 10, 5), D(2026, 10, 13)),  # Mon → Tue of next week
            ("Tuesday", D(2026, 10, 5), D(2026, 10, 6)),
            ("next Monday", D(2026, 10, 4), D(2026, 10, 5)),   # Sun; ISO week starts Mon (R5)
            ("tomorrow", D(2026, 9, 10), D(2026, 9, 11)),
            ("EOD tomorrow", D(2026, 9, 21), D(2026, 9, 22)),
            ("today", D(2026, 9, 10), D(2026, 9, 10)),
            ("EOD", D(2026, 10, 8), D(2026, 10, 8)),           # R1
            ("end of month", D(2026, 9, 14), D(2026, 9, 30)),
            ("end of month", D(2028, 2, 3), D(2028, 2, 29)),
            ("2026-09-08", D(2026, 9, 1), D(2026, 9, 8)),
            ("10 Sep", D(2026, 9, 3), D(2026, 9, 10)),
            ("Oct 3rd", D(2026, 9, 30), D(2026, 10, 3)),
            ("15 October", D(2026, 9, 30), D(2026, 10, 15)),
            ("Wed 16 Sep", D(2026, 9, 14), D(2026, 9, 16)),
            ("2026-10-16", None, D(2026, 10, 16)),             # absolute needs no reference
        ]
        for phrase, meeting, want in cases:
            self.assertEqual(at.resolve_due(phrase, meeting), (want, None), (phrase, meeting))

    def test_unsupported_and_no_reference(self):
        for phrase in ("end of next week", "mid-October", "November", "from 9 Oct",
                       "for Saturday 10 Oct", "10%"):
            self.assertEqual(at.resolve_due(phrase, D(2026, 9, 3)), (None, "DUE_UNSUPPORTED"), phrase)
        for phrase in ("Friday", "tomorrow", "10 Sep", "end of month"):
            self.assertEqual(at.resolve_due(phrase, None), (None, "DUE_NO_REFERENCE"), phrase)

    def test_entry_due_codes(self):
        es = entries("2026-09-03-billing-migration-sync.md")
        plan = find(es, "draft the test plan")
        self.assertEqual((plan["due"], plan["due_raw"], plan["due_code"]),
                         (None, "end of next week", "DUE_UNSUPPORTED"))
        snap = find(es, "snapshot the production DB")
        self.assertEqual((snap["due"], snap["due_code"]), (None, "DUE_MISSING"))
        es = entries("2026-10-03-sales-pipeline.md")
        self.assertEqual(find(es, "chase Orbit Labs")["due_code"], "DUE_NO_REFERENCE")
        es = entries("2026-09-29-billing-cutover-planning.md")
        freeze = find(es, "freeze invoice edits")
        self.assertEqual((freeze["description"], freeze["due_code"]), ("freeze invoice edits", "DUE_UNSUPPORTED"))
        es = entries("2026-10-06-quote-portal-weekly.md")
        self.assertEqual(find(es, "demo the portal")["due"], D(2026, 10, 14))  # "on <date>"


KNOWN = ["Anita Desai", "Karan Joshi", "Lena Fischer", "Meera Iyer", "Priya Nair",
         "Priya Shah", "Rahul Mehta", "Rahul Verma", "Sam Okafor"]
BOTH_PRIYAS = ["Karan Joshi", "Sam Okafor", "Rahul Verma", "Priya Shah", "Priya Nair"]


class Phase4Owners(unittest.TestCase):
    def test_resolution(self):
        r = at.resolve_owner
        self.assertEqual(r("Priya", ["Karan Joshi", "Rahul Verma", "Priya Shah"], KNOWN),
                         ("Priya Shah", "resolved", []))                       # 09-03
        self.assertEqual(r("Priya N.", BOTH_PRIYAS, KNOWN), ("Priya Nair", "resolved", []))
        self.assertEqual(r("Priya S.", BOTH_PRIYAS, KNOWN), ("Priya Shah", "resolved", []))
        self.assertEqual(r("Priya", BOTH_PRIYAS, KNOWN),
                         ("Priya", "ambiguous", ["Priya Nair", "Priya Shah"]))  # 09-17
        self.assertEqual(r("Rahul", ["Rahul Mehta", "Rahul Verma", "Anita Desai"], KNOWN),
                         ("Rahul", "ambiguous", ["Rahul Mehta", "Rahul Verma"]))  # 10-07
        self.assertEqual(r("anita", ["Anita Desai", "Rahul Mehta"], KNOWN), ("Anita Desai", "resolved", []))
        self.assertEqual(r("Priya Shah", [], KNOWN), ("Priya Shah", "resolved", []))  # full name

    def test_no_attendees_is_ambiguous_even_if_unique(self):  # R3
        self.assertEqual(at.resolve_owner("Sam", [], KNOWN), ("Sam", "ambiguous", ["Sam Okafor"]))
        self.assertEqual(at.resolve_owner("Rahul", [], KNOWN),
                         ("Rahul", "ambiguous", ["Rahul Mehta", "Rahul Verma"]))

    def test_unassigned_and_groups(self):
        for raw in (None, "Someone", "TBD"):
            self.assertEqual(at.resolve_owner(raw, KNOWN, KNOWN), ("Unassigned", "unassigned", []))
        for raw in ("Everyone", "Team"):
            self.assertEqual(at.resolve_owner(raw, KNOWN, KNOWN), (raw, "group", []))

    def test_unknown_name_is_not_guessed(self):
        self.assertEqual(at.resolve_owner("Zed", ["Sam Okafor"], KNOWN), ("Zed", "ambiguous", []))


def corpus_tasks(path=NOTES, cli_done=()):
    files, mentions, _ = at.scan(path)
    return at.build_tasks(files, mentions, set(cli_done))


def task(tasks, needle, owner=None):
    hits = [t for t in tasks if needle in t["description"] and (owner is None or t["owner"] == owner)]
    assert len(hits) == 1, (needle, owner, [(t["owner"], t["description"]) for t in hits])
    return hits[0]


class Phase5MergeAndCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = corpus_tasks()

    def test_repeat_is_one_task(self):  # AC8, D1
        t = task(self.tasks, "revised quote to Acme")
        self.assertEqual(t["owner"], "Priya Nair")
        self.assertEqual([m["meeting_date"] for m in t["mentions"]], ["2026-09-01", "2026-09-08", "2026-09-15"])
        self.assertEqual((t["status"], t["done_by"]), ("done", "note"))

    def test_different_wording_is_separate(self):  # AC9, E3
        first = task(self.tasks, "share first wireframes")
        updated = task(self.tasks, "share updated wireframes")
        self.assertEqual(len(first["mentions"]), 1)
        self.assertEqual(len(updated["mentions"]), 2)
        self.assertIn(f"POSSIBLE_DUPLICATE:{first['id']}", updated["warnings"])
        self.assertEqual(updated["due"], "2026-09-24")  # latest mention's due wins (R2)

    def test_merge_keeps_latest_resolvable_due(self):  # AC10
        t = task(self.tasks, "review the schema mapping doc")
        self.assertEqual((t["owner"], len(t["mentions"]), t["due"], t["status"]),
                         ("Rahul Verma", 2, "2026-09-07", "open"))
        sso = task(self.tasks, "SSO work")  # "estimate the SSO work" / "estimate SSO work"
        self.assertEqual((len(sso["mentions"]), sso["due"]), (2, "2026-09-28"))

    def test_later_check_closes_earlier(self):  # AC11, AC6, D2
        for needle, n in [("set up the staging environment", 2), ("renewal call with Acme's CTO", 3),
                          ("approve the Q4 cloud spend", 2), ("export last quarter invoices", 2),
                          ("notify customers about the maintenance window", 2)]:
            t = task(self.tasks, needle)
            self.assertEqual((t["status"], len(t["mentions"])), ("done", n), needle)

    def test_ambiguous_owner_never_merges(self):  # E7, AC13
        self.assertEqual(task(self.tasks, "draft the test plan")["owner"], "Priya Shah")
        self.assertEqual(task(self.tasks, "notify customers")["owner"], "Priya Nair")
        t = task(self.tasks, "sign off the cutover checklist")
        self.assertEqual((t["owner"], t["owner_status"]), ("Priya", "ambiguous"))
        self.assertEqual(t["owner_candidates"], ["Priya Nair", "Priya Shah"])
        self.assertIn("OWNER_AMBIGUOUS", t["warnings"])
        slides = task(self.tasks, "send the slides")  # untitled.md, no attendees (E8)
        self.assertEqual(slides["owner_status"], "ambiguous")
        self.assertIn("DUE_NO_REFERENCE", slides["warnings"])

    def test_multi_owner_and_missing(self):  # R4, AC14
        api = [t for t in self.tasks if "component API" in t["description"]]
        self.assertEqual(sorted(t["owner"] for t in api), ["Anita Desai", "Rahul Mehta"])
        crm = task(self.tasks, "update the CRM notes")
        self.assertEqual((crm["owner"], crm["owner_status"]), ("Unassigned", "unassigned"))
        self.assertIn("OWNER_MISSING", crm["warnings"])
        self.assertIn("OWNER_GROUP", task(self.tasks, "decide on the launch date")["warnings"])

    def test_order_independent(self):
        files, mentions, _ = at.scan(NOTES)
        self.assertEqual(at.build_tasks(files, mentions, set()),
                         at.build_tasks(files, list(reversed(mentions)), set()))


class Phase5Fixtures(TempCwd):
    def tasks(self):
        return corpus_tasks(self.tmp / "fx")

    def test_ambiguous_check_does_not_close(self):  # AC12, E5
        self.note("a.md", "Date: 2026-11-02\nAttendees: Priya Nair, Sam Okafor\n- [ ] Priya to send the slides\n")
        self.note("b.md", "Date: 2026-11-03\nAttendees: Priya Nair, Priya Shah\n- [x] Priya to send the slides\n")
        open_t = task(self.tasks(), "send the slides", owner="Priya Nair")
        self.assertEqual(open_t["status"], "open")
        self.assertTrue(any(w.startswith("UNCONFIRMED_COMPLETION:") for w in open_t["warnings"]))

    def test_loose_match_check_does_not_close(self):  # AC12
        self.note("a.md", "Date: 2026-11-02\nAttendees: Sam Okafor\n- [ ] Sam to write the release notes\n")
        self.note("b.md", "Date: 2026-11-03\nAttendees: Sam Okafor\n- [x] Sam to write release notes draft\n")
        ts = self.tasks()
        self.assertEqual(task(ts, "write the release notes")["status"], "open")
        self.assertTrue(any(w.startswith("UNCONFIRMED_COMPLETION:") for w in task(ts, "write the release notes")["warnings"]))
        self.assertTrue(any(w.startswith("POSSIBLE_DUPLICATE:") for w in task(ts, "notes draft")["warnings"]))

    def test_done_is_never_reopened(self):  # R6
        self.note("a.md", "Date: 2026-11-02\nAttendees: Sam Okafor\n- [x] Sam to ship it\n")
        self.note("b.md", "Date: 2026-11-09\nAttendees: Sam Okafor\n- [ ] Sam to ship it\n")
        self.assertEqual(task(self.tasks(), "ship it")["status"], "done")


STORE = ".actiontrack/tasks.json"


def stored(needle, owner=None):
    data = json.loads(Path(STORE).read_text(encoding="utf-8"))
    return task(data["tasks"], needle, owner)


class Phase6Ingest(TempCwd):
    def test_notes_untouched(self):  # AC1
        before = hashes(self.tmp / "notes")
        run("ingest", "notes", "--today", TODAY)
        run("ingest", "notes", "--today", TODAY)
        self.assertEqual(hashes(self.tmp / "notes"), before)

    def test_malformed_reported_not_fatal(self):  # AC2
        code, out, _ = run("ingest", "notes")
        self.assertEqual(code, at.EXIT_WARN)
        for name, why in [("2026-09-09-notes-final-v2.md", "EMPTY_FILE"), ("scratch.md", "UNDECODABLE"),
                          ("2026-09-19-offsite-draft.txt", "NOT_MARKDOWN"), ("untitled.md", "DATE_MISSING"),
                          ("2026-10-03-sales-pipeline.md", "DATE_MISSING")]:
            self.assertRegex(out, rf"{why}\s+notes/{re.escape(name)}")
        self.assertIn("97 tasks", out)

    def test_idempotent_and_order_independent(self):  # AC3, FR-I6
        run("ingest", "notes")
        first = Path(STORE).read_bytes()
        run("ingest", "notes")
        self.assertEqual(Path(STORE).read_bytes(), first)
        Path(STORE).unlink()
        for p in sorted((self.tmp / "notes").iterdir(), reverse=True):
            run("ingest", f"notes/{p.name}")
        self.assertEqual(Path(STORE).read_bytes(), first)

    def test_edited_note_replaces_its_mentions(self):
        p = self.note("a.md", "Date: 2026-11-02\nAttendees: Sam Okafor\n- [ ] Sam to ship it\n")
        run("ingest", "fx")
        p.write_text("Date: 2026-11-02\nAttendees: Sam Okafor\n- [ ] Sam to ship it today\n", encoding="utf-8")
        run("ingest", "fx")
        data = json.loads(Path(STORE).read_text(encoding="utf-8"))
        self.assertEqual(len(data["mentions"]), 1)
        self.assertEqual(data["tasks"][0]["due"], "2026-11-02")

    def test_store_inside_notes_refused(self):  # FR-I9
        before = hashes(self.tmp / "notes")
        self.assertEqual(run("ingest", "notes", "--store", "notes/tasks.json")[0], at.EXIT_USER)
        self.assertEqual(hashes(self.tmp / "notes"), before)

    def test_missing_path(self):
        self.assertEqual(run("ingest", "nope")[0], at.EXIT_USER)

    def test_corrupt_store_not_overwritten(self):
        Path(STORE).parent.mkdir()
        Path(STORE).write_text("{oops", encoding="utf-8")
        self.assertEqual(run("ingest", "notes")[0], at.EXIT_USER)
        self.assertEqual(Path(STORE).read_text(encoding="utf-8"), "{oops")

    def test_store_contents(self):  # AC4, AC5, AC7
        run("ingest", "notes")
        refund = stored("refund affected customers")
        self.assertEqual((refund["due"], len(refund["mentions"])), (None, 1))
        self.assertEqual(stored("Refresh the brand kit")["mentions"][0]["format"], "table")
        self.assertEqual(stored("case study outline")["mentions"][0]["meeting_date"], "2026-09-16")
        self.assertEqual(stored("case study outline")["mentions"][0]["source_file"],
                         "notes/2026-09-16-standup.md")


def ids(out):
    return set(re.findall(r"^([0-9a-f]{8})\s", out, re.M))


class Phase7ListDone(TempCwd):
    def setUp(self):
        super().setUp()
        run("ingest", "notes")

    def list(self, *a):
        code, out, err = run("list", "--today", TODAY, *a)
        self.assertEqual(code, 0, err)
        return ids(out), out, err

    def test_overdue_boundary(self):  # AC17
        got, _, _ = self.list("--status", "overdue")
        self.assertIn(stored("patch the outdated express")["id"], got)    # due 10-12
        self.assertNotIn(stored("reconcile September ledger")["id"], got)  # due today
        self.assertNotIn(stored("enable MFA")["id"], got)                  # due 10-16
        self.assertNotIn(stored("revised quote to Acme")["id"], got)       # done

    def test_missing_due(self):  # AC18, D5
        vpn = stored("share the VPN guide")["id"]
        self.assertIn(vpn, self.list("--status", "open")[0])
        self.assertNotIn(vpn, self.list("--status", "overdue")[0])
        self.assertNotIn(vpn, self.list("--due-before", "2099-01-01")[0])
        self.assertIn(vpn, self.list()[0])

    def test_open_includes_overdue_and_excludes_done(self):  # FR-L3
        opened = self.list("--status", "open")[0]
        self.assertTrue(self.list("--status", "overdue")[0] <= opened)
        self.assertNotIn(stored("revised quote to Acme")["id"], opened)
        self.assertIn(stored("revised quote to Acme")["id"], self.list("--status", "done")[0])

    def test_owner_filter(self):  # AC19
        got, _, err = self.list("--owner", "priya")
        self.assertIn("several people", err)
        self.assertIn(stored("draft the test plan")["id"], got)         # Priya Shah
        self.assertIn(stored("prepare the renewal one-pager")["id"], got)  # Priya Nair
        self.assertIn(stored("sign off the cutover")["id"], got)        # ambiguous Priya
        shah, _, err = self.list("--owner", "Priya Shah")
        self.assertEqual(err, "")
        self.assertIn(stored("draft the test plan")["id"], shah)
        self.assertNotIn(stored("prepare the renewal one-pager")["id"], shah)
        self.assertEqual(self.list("--owner", "Unassigned")[0],
                         {stored("update the CRM notes")["id"], stored("design-system changelog")["id"]})
        self.assertEqual(self.list("--owner", "Nobody Here")[0], set())

    def test_combined_filters(self):  # AC13-style
        got, out, _ = self.list("--owner", "Karan", "--due-before", "2026-10-10")
        self.assertTrue(got)
        for line in out.splitlines()[1:]:
            self.assertRegex(line, r"^\w{8}\s+Karan Joshi\s+2026-(09-\d\d|10-0\d)\s")

    def test_done_persists(self):  # AC20
        tid = stored("share the VPN guide")["id"]
        before = hashes(self.tmp / "notes")
        self.assertEqual(run("done", tid)[0], 0)
        self.assertEqual(stored("share the VPN guide")["status"], "done")
        run("ingest", "notes")
        t = stored("share the VPN guide")
        self.assertEqual((t["status"], t["done_by"]), ("done", "cli"))
        self.assertEqual(hashes(self.tmp / "notes"), before)
        code, out, _ = run("done", tid)
        self.assertEqual(code, 0)
        self.assertIn("already done", out)

    def test_done_unknown_changes_nothing(self):  # FR-D2
        before = Path(STORE).read_bytes()
        good = stored("share the VPN guide")["id"]
        self.assertEqual(run("done", good, "NOPE")[0], at.EXIT_USER)
        self.assertEqual(Path(STORE).read_bytes(), before)


OUTBOX = Path(".actiontrack/outbox")


class Phase8Followup(TempCwd):
    def setUp(self):
        super().setUp()
        run("ingest", "notes")
        shutil.copy(ROOT / "people.example.yaml", "people.yaml")

    def followup(self, *a):
        return run("followup", "--today", TODAY, *a)

    def outbox(self):
        return sorted(OUTBOX.glob("*.json")) if OUTBOX.exists() else []

    def test_missing_people_file(self):  # AC25, FR-F7
        os.remove("people.yaml")
        code, _, err = self.followup()
        self.assertEqual(code, at.EXIT_USER)
        self.assertIn("people.example.yaml", err)
        self.assertEqual(self.outbox(), [])

    def test_dry_run_writes_nothing(self):  # AC21
        code, out, _ = self.followup("--dry-run")
        self.assertEqual(self.outbox(), [])
        self.assertIn('"to": "rahul.verma@example.com"', out)
        self.assertIn("no files written", out)

    def test_one_file_per_owner_never_overwritten(self):  # AC22, FR-F8
        self.followup()
        files = self.outbox()
        overdue = [t for t in json.loads(Path(STORE).read_text(encoding="utf-8"))["tasks"]
                   if at.is_overdue(t, at.dt.date.fromisoformat(TODAY)) and t["owner_status"] == "resolved"]
        owners = {t["owner"] for t in overdue}
        self.assertEqual(len(files), len(owners))
        payloads = [json.loads(p.read_text(encoding="utf-8")) for p in files]
        self.assertEqual({p["owner"] for p in payloads}, owners)
        for p in payloads:
            self.assertEqual(set(p), {"status", "to", "owner", "subject", "body", "task_ids", "generated_at"})
            self.assertEqual(p["status"], "pending_gmail_draft")
            self.assertEqual(sorted(p["task_ids"]), sorted(t["id"] for t in overdue if t["owner"] == p["owner"]))
            for tid in p["task_ids"]:
                self.assertIn(f"[{tid}]", p["body"])
        snapshot = {p: p.read_bytes() for p in files}
        self.followup()
        self.assertEqual(len(self.outbox()), 2 * len(files))
        self.assertEqual({p: p.read_bytes() for p in files}, snapshot)

    def test_skipped_owners_reported(self):  # AC23, FR-F4
        Path("people.yaml").write_text(
            "".join(l for l in (ROOT / "people.example.yaml").read_text().splitlines(True) if "Lena" not in l))
        code, out, _ = self.followup("--status", "open")
        self.assertEqual(code, at.EXIT_WARN)
        self.assertRegex(out, r"OWNER_MISSING\s+\w{8}")
        self.assertRegex(out, r"OWNER_AMBIGUOUS\s+\w{8}.*Priya Nair, Priya Shah")
        self.assertRegex(out, r"OWNER_GROUP\s+\w{8}")
        self.assertIn("NO_EMAIL", out)
        self.assertIn("follow-up cannot proceed for Lena Fischer", out)
        owners = {json.loads(p.read_text(encoding="utf-8"))["owner"] for p in self.outbox()}
        self.assertNotIn("Lena Fischer", owners)
        self.assertFalse(owners & {"Unassigned", "Priya", "Rahul", "Sam", "Everyone", "Team"})

    def test_no_network_no_send_honest_output(self):  # AC24, FR-F3, FR-F5
        import socket
        from unittest import mock
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            code, out, err = self.followup()
        self.assertIn(code, (at.EXIT_OK, at.EXIT_WARN), err)
        self.assertIn("not yet created in Gmail", out)
        self.assertNotRegex(out.replace("not yet created in Gmail", ""), r"(?i)created in gmail|sent")
        src = (ROOT / "actiontrack.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^\s*(import|from)\s+(socket|smtplib|urllib|http|requests|email)\b")
        self.assertNotRegex(src, r"\bsend\s*\(|\.send\b|drafts\.|messages\.")

    def test_nothing_selected(self):
        code, out, _ = self.followup("--owner", "Nobody Here")
        self.assertEqual(code, at.EXIT_OK)
        self.assertEqual(self.outbox(), [])


if __name__ == "__main__":
    unittest.main()
