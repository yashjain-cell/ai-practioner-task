# actiontrack — Task Checklist

Order: write the test first, then the code. Each item names the requirement (FR / AC / E) it satisfies. Don't start Phase 1 until Phase 0 is signed off.

## Phase 0 — Spec sign-off
- [x] Draft requirements.md (Rev 1) from the real notes
- [x] Apply decisions D1–D8 (Rev 2), and write design.md and tasks.md
- [x] R1–R8: defaults accepted unchanged
- [x] R0: the MVP writes outbox draft files only, and live Gmail is deferred (Phase 10)
- [x] Coverage check: all four commands (ingest P2–P6, list P7, done P7, followup P8) and the edge cases E1–E14 have tasks (see the mapping at the bottom)

## Phase 1 — Skeleton
- [x] `pyproject.toml` (console script `actiontrack`), `.gitignore` (`people.yaml`, `.actiontrack/`)
- [x] `people.example.yaml` with the 9 people seen in the notes and `example.com` addresses (R7)
- [x] `actiontrack.py` argparse with `ingest|list|done|followup`, `--store/--people/--today`, and exit codes
- [x] `test_actiontrack.py` harness: copy `notes/` to a temporary directory and hash its files (AC1)

## Phase 2 — Parsing (FR-I1–I3, FR-I8)
- [x] Test + implement the file checks: `NOT_MARKDOWN`, `EMPTY_FILE`, `UNDECODABLE` (AC2)
- [x] Test + implement the headers: key lines, YAML front matter, the 3 date formats, `DATE_MISSING`, `ATTENDEES_MISSING` (AC2, AC7)
- [x] Test + implement removal of pseudo-task regions: code fences, HTML comments, blockquotes (AC4)
- [x] Test + implement checkbox extraction: `-*+`, `[ ]/[x]/[X]`, nested items (AC6)
- [x] Test + implement table-row extraction (`Owner` + `Action`/`Task` [+ `Due`]) (AC5)
- [x] Test + implement `ACTION:` lines (AC5)
- [x] Test + implement the owner/description/due split, plus fixtures for `MALFORMED_ENTRY` (E13)

## Phase 3 — Due dates (D6, requirements §7)
- [x] Table-driven test over every §7 example row (AC15)
- [x] Fixtures: same-day Friday, `today`, `next Tuesday`, end of month in February, `EOD` (R1), `from`/`for` → unsupported (R1)
- [x] `DUE_UNSUPPORTED` keeps `due_raw`. `DUE_NO_REFERENCE` when the meeting date is missing (AC16)

## Phase 4 — Owners (D4, D7, E6–E8)
- [x] Tests: 09-03 "Priya" → Shah. 09-29 "Priya N." → Nair. 09-17 "Priya" ambiguous. 10-07 "Rahul" ambiguous (AC13)
- [x] Tests: no attendee list → ambiguous (R3). `Someone`/`TBD` → `Unassigned` (AC14)
- [x] Tests: `@anita`, `Anita & Rahul` → 2 mentions, `Lena (with Priya)`, `Everyone`/`Team` → `OWNER_GROUP` (R4)

## Phase 5 — Merge and completion (D1, D2)
- [x] Test + implement `normalise` and `task_id` (design §3)
- [x] Duplicate detection: revised quote → 1 task, 3 mentions (AC8). Wireframes → 2 tasks + `POSSIBLE_DUPLICATE` (AC9). Schema doc due date (AC10)
- [x] Completion propagation: staging and renewal call closed by a later `[x]` (AC11). Karan's Q4 cloud spend closed across files (AC6)
- [x] No auto-close: an ambiguous-owner `[x]` and a loose-match `[x]` → `UNCONFIRMED_COMPLETION` (AC12)
- [x] `done` is never reopened (R6)

## Phase 6 — Store and ingest (FR-I4–I10)
- [x] Atomic JSON store. Replace mentions per file. Rebuild tasks.
- [x] Idempotency: a second ingest gives a byte-identical store (AC3). Ingest order doesn't matter.
- [x] A `cli_done` entry survives re-ingest (FR-I7)
- [x] Ingest summary and exit code 2 on warnings (FR-I10)
- [x] The full-corpus run leaves the notes unchanged (AC1)

## Phase 7 — list / done (FR-L*, FR-D*)
- [x] Overdue boundary: due before today vs. due today vs. due in the future (AC17)
- [x] Missing due date: open but not overdue, and excluded by `--due-before` (AC18)
- [x] `--owner` full name / first name / ambiguity notice / `Unassigned`. Invalid filter → exit 1 (AC19)
- [x] `done` with a known ID, an unknown ID, an already-done task. Re-ingest keeps it done (AC20)

## Phase 8 — followup: draft FILE generation only (FR-F1–F8, design §9a)
- [x] Load `people.yaml` (flat `Full Name: email`, stdlib parser). Missing file → exit 1 (AC25)
- [x] Build payloads and group by owner. Skip with reporting for `OWNER_MISSING`/`OWNER_AMBIGUOUS`/`OWNER_GROUP`/`NO_EMAIL`, exit 2 (AC23)
- [x] `--dry-run` prints and writes no files (AC21)
- [x] One JSON file per owner in `outbox/`, opened with mode `x` so nothing is overwritten (AC22)
- [x] No network or send: sockets patched to raise, static scan, output says "not yet created in Gmail" (AC24)

## Phase 9 — MVP acceptance
- [x] All of AC1–AC25 pass (`python -m unittest`)
- [x] Run `ingest notes/`, `list --status overdue`, `followup --dry-run` on the real corpus and review the output by hand

## Phase 10 — DEFERRED: live Gmail drafts (separate follow-up, design §9b, requirements §12)
- [ ] Authenticate the claude.ai Gmail MCP connector (user action)
- [ ] Check its draft-creation tool's name and parameters, and record them in design.md §9b
- [ ] Agree the session workflow: outbox file → draft tool only → set the status to `gmail_draft_created`
- [ ] AC-G1: a draft exists per outbox file. AC-G2: nothing appears in Sent. AC-G3: an unavailable connector gives a clear error with no fallback

## Coverage map
| Command / edge case | Phase |
|---|---|
| `ingest` | 2, 3, 4, 5, 6 |
| `list` (owner, status, due-before) | 7 |
| `done` | 7 |
| `followup` (draft files) | 8 |
| E1 re-ingest, E2 repeats, E3 different wording | 5, 6 |
| E4 later `[x]`, E5 unconfirmed completion | 5 |
| E6 missing owner, E7/E8 ambiguous owners | 4 |
| E9 missing due date, E10 unsupported due phrase, E11 no reference date | 3, 7 |
| E12 malformed files, E13 malformed entries, E14 pseudo-tasks | 2 |
