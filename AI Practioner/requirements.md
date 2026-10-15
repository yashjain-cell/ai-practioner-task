# actiontrack — Requirements (MVP)

Status: **Rev 3, all decisions applied.** D1–D8 and R0–R8 are decided (§9, §10). In the MVP, `followup` writes draft **files** only. Creating drafts in Gmail is deferred (§12). Related docs: [`design.md`](design.md) (spec and implementation plan) and [`tasks.md`](tasks.md) (checklist).

## 1. Problem and goal

**Problem.** Action items from meetings are spread across Markdown notes in `notes/` (34 `.md` files plus one `.txt`, dated 2026-09-01 → 2026-10-14). The same item is often repeated across weekly meetings with slightly different wording, and it gets ticked `[x]` in a later note. Nobody can answer "what is open, who owns it, what is overdue?" without reading every file.

**Goal.** A local CLI, `actiontrack`, that:
1. extracts action items into a task store, merging repeats of the same item,
2. lists them with filters,
3. marks them done,
4. writes follow-up email drafts as **files** in `.actiontrack/outbox/`. Nothing is ever sent. Turning those files into real Gmail drafts is a separate, deferred step (§12).

**Non-negotiable:** `actiontrack` never modifies, moves or deletes any file under the notes directory. Every task keeps references back to the note lines it came from, so it can be audited.

## 2. Observed note formats (source of truth for the parser)

### 2.1 Header formats
| Format | Example files | Shape |
|---|---|---|
| A. Heading + key lines | most files | `# Meeting: <title>` / `Date: <date>` / `Attendees: <comma list>` |
| B. YAML front matter | `2026-09-16-standup.md`, `2026-09-30-standup.md` | `---` / `title:` / `date:` / `attendees: [a, b]` / `---` |

### 2.2 Header date formats
- ISO `2026-09-01`
- `9 September 2026`
- `Monday, 14 September 2026`
- Unparseable: `sometime last week` (`2026-10-03-sales-pipeline.md`)

### 2.3 Action-item formats (all three are supported, D3)
1. **Checkbox:** `- [ ]`, `- [x]`, `- [X]`, `* [ ]`, `+ [ ]`. These are ingested from **any** section, nested items included.
2. **Table row:** a Markdown table whose header row has an `Owner` column and an `Action` or `Task` column. A `Due` column is optional. Example: `2026-09-30-q4-offsite.md` → `| Anita | Refresh the brand kit | 2026-10-09 |`. Table rows are always `open`.
3. **`ACTION:` line:** `ACTION: <Owner> to <description> …`. Example: `ACTION: Karan to circulate the offsite summary tomorrow`. Always `open`.

Sentence shape for formats 1 and 3: `<Owner> to <description> [<due phrase>]`. The due phrase is introduced by `by`, `on` or `due` (sometimes after `—`), or it is a bare trailing `today` or `tomorrow`.

### 2.4 Lines that look like tasks but are not (must NOT become tasks)
- A checkbox inside a blockquote (`2026-09-21-incident-review.md`)
- A checkbox inside a fenced code block (`2026-10-09-security-review.md`)
- A checkbox inside an HTML comment (`2026-10-09-security-review.md`)
- Ordinary prose, e.g. "Priya offered to help recruit" (`2026-09-18`)

### 2.5 Owner shapes
| Shape | Example | Handling |
|---|---|---|
| First name | `Priya to …` | Resolve through attendees (§5, E7) |
| First name + initial | `Priya N. to` | Match the initial to an attendee's surname |
| Handle | `@anita:` | Treated like the first name `Anita` |
| Placeholder | `Someone to`, `TBD to` | `Unassigned` (D4) |
| No owner prefix | the line doesn't start with `<Name> to` | `Unassigned` (D4) |
| Two owners | `Anita & Rahul to` | **Open:** R4 |
| Owner + helper | `Lena (with Priya) to` | **Open:** R4 |
| Group | `Everyone to`, `Team to` | **Open:** R4 |

### 2.6 People seen in notes
| First name | Full names |
|---|---|
| Priya | **Priya Nair**, **Priya Shah** |
| Rahul | **Rahul Mehta**, **Rahul Verma** |
| Anita, Sam, Karan, Meera, Lena | Anita Desai, Sam Okafor, Karan Joshi, Meera Iyer, Lena Fischer |

No note contains an email address. Email addresses come from `people.yaml` (D7).

### 2.7 Malformed or non-note files
| File | Problem | Handling |
|---|---|---|
| `2026-09-09-notes-final-v2.md` | Empty | Skip and report `EMPTY_FILE` |
| `scratch.md` | Binary / invalid UTF-8 | Skip and report `UNDECODABLE` |
| `2026-09-19-offsite-draft.txt` | Not `.md` | Skip and report `NOT_MARKDOWN` |
| `untitled.md` | No header or date | Ingest its items and report `DATE_MISSING` |
| `2026-10-03-sales-pipeline.md` | Date `sometime last week` | Ingest its items and report `DATE_MISSING` |
| `2026-09-23-standup.md` | No attendees | Ingest its items and report `ATTENDEES_MISSING` |

## 3. Task data model

A **task** is one underlying action item. A **mention** is one place that task appears in a note. A task has one or more mentions (D1).

**Task**
| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable across re-ingest (design §3) |
| `owner` | string | Resolved full name, `Unassigned`, or the raw first name when the owner is ambiguous |
| `owner_status` | `resolved`, `unassigned`, `ambiguous` or `group` | Used by the `followup` eligibility rules |
| `owner_candidates` | list | Filled only when the owner is ambiguous, e.g. `["Priya Nair","Priya Shah"]` |
| `description` | string | Taken from the latest mention |
| `due` | `YYYY-MM-DD` or null | From the latest mention that has a due date that could be resolved (R2) |
| `status` | `open` or `done` | `overdue` is computed, never stored |
| `done_by` | `note`, `cli` or null | How it was closed |
| `mentions` | list | At least one entry |
| `warnings` | list of codes | See §6 |

**Mention**: `source_file` (relative to the working directory, using `/` separators, so the same file gets the same path however `ingest` is pointed at it), `source_line`, `meeting_date` (ISO or null), `format` (`checkbox`/`table`/`action`), `checked` (bool), `owner_raw`, `due_raw`, `due` (resolved or null).

## 4. Commands and functional requirements

Global options:
- `--store <path>`: default `.actiontrack/tasks.json`.
- `--people <path>`: default `./people.yaml`.
- `--today <YYYY-MM-DD>`: **test-only** override of the local system date (D-rule "overdue").

Exit codes: `0` success, `1` user error, `2` completed with warnings (ingest and followup).

### 4.1 `actiontrack ingest <path>`
- **FR-I1** Takes a file or a directory. A directory is scanned recursively for `*.md` files. Other extensions are skipped and reported.
- **FR-I2** Parses header formats A and B and the header date formats in §2.2.
- **FR-I3** Extracts all three item formats (§2.3) into the same mention structure. Ignores the pseudo-tasks in §2.4.
- **FR-I4** Matches each mention to an existing task (D1, design §4). If it matches confidently, the mention is added to that task. If not, a new task is created.
- **FR-I5** A checked mention (`[x]`/`[X]`) closes its matched task, whichever note introduced the task (D2). It never closes a task that has an ambiguous owner or was matched only loosely.
- **FR-I6** **Idempotent.** A mention is identified by (`source_file`, `source_line`, line text). Ingesting it again changes nothing. Ingesting the same files twice gives a byte-identical store.
- **FR-I7** A `done` set by the CLI survives re-ingest.
- **FR-I8** Bad files and entries are reported and skipped. Ingest never stops early (§2.7, §6).
- **FR-I9** Never writes to anything under `<path>`.
- **FR-I10** Prints a summary: files scanned/skipped, tasks created/updated/closed, mentions added, and a count per warning code.

### 4.2 `actiontrack list [--owner <name>] [--status open|done|overdue] [--due-before <YYYY-MM-DD>]`
- **FR-L1** Columns: ID, owner, description, due date, status, and source files (all mentions).
- **FR-L2** Filters are combined with AND.
- **FR-L3** **Overdue** = `status == open` AND `due != null` AND `due < today`. `today` is the local system date. `--status open` includes overdue tasks. Tasks with no due date appear under `open` and are never overdue (D5).
- **FR-L4** `--owner` matches a full name or a first name, case-insensitively. A first name that matches several people returns all their tasks and prints a notice. `--owner Unassigned` lists tasks with no owner.
- **FR-L5** `--due-before D` matches tasks with `due < D`. Tasks with no due date are excluded.
- **FR-L6** An invalid status or date value → exit 1.

### 4.3 `actiontrack done <id> [<id>…]`
- **FR-D1** Updates the store only (`done_by=cli`). It never edits notes.
- **FR-D2** Unknown ID → exit 1, with no change.
- **FR-D3** A task that is already done → no-op, exit 0.

### 4.4 `actiontrack followup [--owner <name>] [--status open|overdue] [--dry-run]`
- **FR-F1** Selects tasks with the same rules as `list`. The default is `--status overdue`.
- **FR-F2** Writes **one draft file per eligible owner** to `<store dir>/outbox/` (by default `.actiontrack/outbox/`). Each file is JSON with the fields `to`, `owner`, `subject`, `body`, `task_ids`, `generated_at` and `"status": "pending_gmail_draft"`. The body lists each task's ID, description, due date and source files.
- **FR-F3** **No email is ever sent, and the CLI makes no network calls.** The MVP CLI does not talk to Gmail at all.
- **FR-F4** The owner has to be a resolved full name with an email address in `people.yaml`. Otherwise no file is written for that owner, and the owner is reported:
  - `Unassigned`: report `OWNER_MISSING`
  - ambiguous: report `OWNER_AMBIGUOUS` with the candidates
  - a group owner: report `OWNER_GROUP`
  - no email address: report `NO_EMAIL` ("follow-up cannot proceed for <owner>")
- **FR-F5** The output must say that draft **files** were written and are **not** yet Gmail drafts. It must never say a Gmail draft was created.
- **FR-F6** `--dry-run` prints the payloads and writes no files.
- **FR-F7** If `people.yaml` is missing → exit 1, with a message pointing to `people.example.yaml`.
- **FR-F8** Each run writes new files and never overwrites an existing one. There is no dedupe across runs (R-defaults).
- Exit code: `0` if every selected owner got a file, `2` if any owner was skipped.

## 5. Edge-case requirements

| # | Case | Required behaviour | Real example (expected) |
|---|---|---|---|
| E1 | Re-ingesting the same files | No new tasks or mentions | any file |
| E2 | Same item in several meetings | **One** task with several mentions | "Priya to send the revised quote to Acme by Friday" (09-01), "Priya N. to send revised quote to Acme — by Friday" (09-08) and "[x] Priya to send the revised quote to Acme" (09-15) → **1 task**, owner Priya Nair, 3 mentions, `done` |
| E3 | Similar wording that is actually a different task | Separate tasks | "Anita to share **first** wireframes" (09-01) and "Anita to share **updated** wireframes" (09-15, 09-22) → **2 tasks**. The 09-15 and 09-22 mentions merge. |
| E4 | Closed in a later note | Close the earlier task | "Rahul to set up the staging environment by 2026-09-08" (09-01) + `[x]` (09-08) → `done` |
| E5 | `[x]` with an ambiguous owner or a loose match | Do **not** close. Report `UNCONFIRMED_COMPLETION`. | test fixture (no real case) |
| E6 | Missing owner | `owner=Unassigned`, `OWNER_MISSING` | "Someone to update the CRM notes" (09-10), "TBD to write the design-system changelog" (09-18) |
| E7 | Ambiguous first name | Resolve through that note's attendees, then the initial. If still unclear → keep the raw name, add `OWNER_AMBIGUOUS` with candidates, and never merge or close by it. | Resolved: 09-03 "Priya" → Priya Shah (the only Priya attending); 09-29 "Priya N." → Priya Nair. Ambiguous: 09-17 "Priya to sign off…", 10-08 "Priya to circulate…", 10-07 "Rahul to review Anita's PR", 09-30 "Rahul to prepare the architecture overview" |
| E8 | Ambiguous with no attendee list | Ambiguous (see R3) | 09-23 "Rahul to rebase…", `untitled.md` "Priya to send the slides…" |
| E9 | Missing due date | `due=null`, `DUE_MISSING`. Appears under open, never overdue. | "Sam to share the VPN guide" (09-02) |
| E10 | Unsupported due phrase | `due=null`, keep `due_raw`, report `DUE_UNSUPPORTED` | "by end of next week" (09-03), "by mid-October" (09-30), "for November" (10-14) |
| E11 | Relative due date with no meeting date | `due=null`, report `DUE_NO_REFERENCE` | `2026-10-03-sales-pipeline.md` "Meera to chase Orbit Labs by Friday" |
| E12 | Malformed file | Skip and report. Other files still ingest. | §2.7 |
| E13 | Malformed entry | Skip and report `MALFORMED_ENTRY` | empty checkbox `- [ ]`, a table row with an empty Action cell (fixtures) |
| E14 | Pseudo-tasks | Not ingested | §2.4 |

## 6. Warning and report codes
`EMPTY_FILE`, `UNDECODABLE`, `NOT_MARKDOWN`, `DATE_MISSING`, `ATTENDEES_MISSING`, `MALFORMED_ENTRY`, `OWNER_MISSING`, `OWNER_AMBIGUOUS`, `DUE_MISSING`, `DUE_UNSUPPORTED`, `DUE_NO_REFERENCE`, `POSSIBLE_DUPLICATE`, `UNCONFIRMED_COMPLETION`, `NO_EMAIL`.

## 7. Due-date resolution rules (D6)

The reference date is the **meeting date**, never the system date. Results are stored in ISO format.

| Phrase | Rule | Real example → expected |
|---|---|---|
| `by <weekday>` (full or 3-letter) | The next occurrence of that weekday, **including the meeting day** | 09-01 (Tue) "by Friday" → 2026-09-04. 09-18 (Fri) "by Mon" → 2026-09-21. A Friday meeting saying "by Friday" → the same day (fixture). |
| `next <weekday>` | That weekday in the **following** calendar week (weeks start Monday, R5) | 10-05 (Mon) "by next Tuesday" → 2026-10-13 |
| `tomorrow` | The meeting date + 1 | 09-10 "by tomorrow" → 2026-09-11. 09-21 "by EOD tomorrow" → 2026-09-22 |
| `today` | The meeting date | fixture |
| `end of month` | The last day of the meeting's month | 09-14 "by end of month" → 2026-09-30 |
| ISO date | As written | "by 2026-09-08" → 2026-09-08 |
| Day + month, no year | The meeting's year | "by 10 Sep" → 2026-09-10. "by Oct 3rd" → 2026-10-03. "results due 15 October" → 2026-10-15. "by Wed 16 Sep" → 2026-09-16 |
| Anything else | `due=null` + `DUE_UNSUPPORTED` | "end of next week", "mid-October", "for November", "EOD" (R1), "from 9 Oct" (R1) |

## 8. Acceptance criteria

Tests run against a **temporary copy** of `notes/` plus small fixtures, with `--today 2026-10-15`.

**Safety and robustness**
- **AC1** After `ingest`, every file under the notes directory has the same SHA-256 hash, and no file has been added or removed.
- **AC2** `ingest notes/` exits 2. It reports `EMPTY_FILE` (`2026-09-09-notes-final-v2.md`), `UNDECODABLE` (`scratch.md`), `NOT_MARKDOWN` (`.txt`), and `DATE_MISSING` (`untitled.md`, `2026-10-03-sales-pipeline.md`). It still ingests every other file.
- **AC3** Running `ingest` twice gives a byte-identical store.
- **AC4** No task is created from the blockquote, code-fence or HTML-comment lines (§2.4). Exactly one task exists for "Karan to refund affected customers" (from the Action items section), with `due=null`.

**Formats**
- **AC5** The `2026-09-30-q4-offsite.md` table row gives a task: owner Anita Desai, "Refresh the brand kit", due 2026-10-09. The `ACTION:` line gives: owner Karan Joshi, "circulate the offsite summary", due 2026-10-01 (the meeting was on 09-30, so "tomorrow" is 10-01).
- **AC6** `2026-10-02-finance-sync.md`: the `[X]` item closes the "Karan to approve the Q4 cloud spend" task from `2026-09-30-standup.md` (1 task, 2 mentions, `done`). The `*` and `+` bullets are ingested.
- **AC7** YAML-header notes (`2026-09-16-standup.md`) give mentions with `meeting_date=2026-09-16`.

**Duplicates and completion**
- **AC8** E2: the revised-quote item gives exactly 1 task with 3 mentions, status `done`, `done_by=note`.
- **AC9** E3: the first-wireframes and updated-wireframes items give 2 separate tasks. The updated-wireframes task has 2 mentions (09-15, 09-22).
- **AC10** "Rahul to review the schema mapping doc" (09-03 with "by Monday", 09-17 with no due date) gives 1 task, owner Rahul Verma, 2 mentions, due 2026-09-07, status `open`, overdue.
- **AC11** E4: the staging-environment task is `done`. "Meera to book the renewal call with Acme's CTO" (09-10, 09-28, `[x]` 10-14) gives 1 task, `done`.
- **AC12** E5 fixture: an ambiguous-owner `[x]` mention does not close any task, and the run reports `UNCONFIRMED_COMPLETION`.

**Owners**
- **AC13** 09-03 "Priya to draft the test plan…" → Priya Shah. 09-29 "Priya N. to notify…" → Priya Nair. 09-17 "Priya to sign off the cutover checklist" → owner `Priya`, `OWNER_AMBIGUOUS`, candidates {Priya Nair, Priya Shah}.
- **AC14** "Someone to update the CRM notes" → owner `Unassigned`, `OWNER_MISSING`.

**Dates**
- **AC15** Every "Real example → expected" row in §7 resolves as shown. Fixtures cover a same-day Friday and `today`.
- **AC16** "by end of next week" → `due=null`, `due_raw` kept, `DUE_UNSUPPORTED`. `sales-pipeline` "by Friday" → `due=null`, `DUE_NO_REFERENCE`.

**List and done**
- **AC17** `list --status overdue` includes "Rahul to patch the outdated express dependency" (due 2026-10-12). It excludes "Karan to reconcile September ledger" (due 2026-10-15, today) and "Sam to enable MFA…" (due 2026-10-16).
- **AC18** `list --status open` includes "Sam to share the VPN guide" (no due date). `list --status overdue` and `list --due-before 2099-01-01` exclude it.
- **AC19** `list --owner priya` prints an ambiguity notice and returns both Priyas' tasks. `list --owner "Priya Shah"` returns only hers. `list --status bogus` → exit 1.
- **AC20** `done <id>` → `done`. It stays `done` after re-ingest, and the notes are unchanged (AC1). `done NOPE` → exit 1.

**Followup (MVP: draft files only, all tested)**
- **AC21** `followup --dry-run` prints one payload per eligible owner and writes no files.
- **AC22** `followup` writes exactly one JSON file per eligible owner to the outbox, containing the fields in FR-F2, and the body lists every selected task's ID. A second run adds new files and leaves the first ones unchanged.
- **AC23** Owners that are `Unassigned`, ambiguous, a group, or have no email address get no file and are reported with `OWNER_MISSING`, `OWNER_AMBIGUOUS`, `OWNER_GROUP` or `NO_EMAIL`. The exit code is 2.
- **AC24** Nothing is sent and the CLI stays off the network. With sockets patched to raise an error, `followup` still succeeds. A static scan of `actiontrack.py` finds no `smtplib`, `socket`, `urllib`, `http.client` or any `send(` call. The output contains "not yet created in Gmail" and never claims a Gmail draft exists.
- **AC25** `followup` without `people.yaml` → exit 1, with a message mentioning `people.example.yaml`.

**Deferred, not part of the MVP test suite (§12):** AC-G1 a Gmail draft exists for each outbox file. AC-G2 nothing appears in Sent. AC-G3 an unavailable or unauthenticated connector produces a clear error with no fallback.

## 9. Decisions (applied)
| ID | Decision |
|---|---|
| D1 | A repeated item is **one task** with a list of mentions (source file, line, meeting date). Different wording or context → a separate task. |
| D2 | A later `[x]` closes the matched task, whatever note introduced it. Ambiguous or loose matches are never auto-closed. |
| D3 | Checkboxes, task rows in tables, and `ACTION:` lines all map to one structure. Prose is ignored. |
| D4 | No owner → `Unassigned`. The tool never guesses. |
| D5 | No due date → `null`. Appears under open, never overdue. |
| D6 | Relative dates are resolved from the meeting date (§7). Ambiguous or unsupported phrases → null + report. |
| D7 | `people.yaml` at the project root maps full names to email addresses. Owners are resolved using attendees. If an owner is ambiguous or has no email address, no draft is created and it is reported. |
| D8 | Gmail goes through the existing Gmail MCP connector, with no credentials in code, CLI or store. If it is missing or unauthenticated → a clear error, with no substitute integration. Drafts only, never sent. **Narrowed by R0:** the MVP CLI only writes draft files, and the connector step is deferred (§12). |
| Rules | Re-ingest never duplicates. Source references are kept. Overdue uses the local system date. Malformed input is reported and skipped. |

## 10. Resolved follow-ups (all decided; the defaults below were accepted unchanged)
| ID | Question | Decision |
|---|---|---|
| **R0** | How does the CLI reach Gmail? The connector is a claude.ai connector that a standalone process can't call, and as of 2026-10-15 it is not authenticated. | The MVP CLI **only writes draft files** to `.actiontrack/outbox/`. Creating live Gmail drafts is deferred to §12. |
| R1 | `by EOD`, `from 9 Oct` (a start date, not a deadline?) and `for Saturday 10 Oct` aren't covered by D6. | `EOD` = the meeting date. `from`/`for` → unsupported, null + report |
| R2 | When merged mentions disagree on the due date, which wins? | The latest mention with a due date that could be resolved. Earlier ones stay in the mentions list. |
| R3 | When a note has no attendees, may a first name be resolved if it is unique in `people.yaml` (e.g. 09-23 "Sam" → Sam Okafor)? | No. Mark it ambiguous (strict reading of D7) |
| R4 | `Anita & Rahul`, `Lena (with Priya)`, `Everyone`, `Team`? | `&` → one task per person. `(with X)` → the first person owns it, and the helper is kept in the description. `Everyone`/`Team` → owner kept as written, report `OWNER_GROUP`, no draft |
| R5 | Week boundary for "next <weekday>": Monday or Sunday start? | Monday (ISO) |
| R6 | Can a closed task be reopened by a later open `[ ]` mention? | No. `done` is final in the MVP |
| R7 | Who provides the real `people.yaml` email addresses? | The repo ships `people.example.yaml` with `example.com` placeholders, and `people.yaml` is git-ignored |
| R8 | Should a meeting with an unparseable date use the date in its file name? | No (don't guess). Report `DUE_NO_REFERENCE` |

## 11. Out of scope (MVP)
- Sending email, or scheduling sends.
- Creating live Gmail drafts (deferred to §12).
- Writing back to notes.
- Extracting tasks from free prose with NLP or an LLM, or fuzzy auto-merging (similar matches are only flagged `POSSIBLE_DUPLICATE`).
- Calendar, Slack, Jira or CRM integrations, file watching, a web UI, multi-user stores.
- Editing or reopening tasks, priorities, tags, `--json` output.
- Non-Markdown inputs and timezones.

## 12. Deferred: live Gmail draft creation (separate follow-up task)
This step turns the outbox files into real Gmail drafts. It starts only when **all** of these hold:
1. The claude.ai Gmail MCP connector is authenticated.
2. Its draft-creation tool name and parameters are checked and recorded in `design.md` §9.
3. There is an agreed workflow: a Claude Code session reads `outbox/*.json` and calls **only** the draft-creation tool. After a draft is created, its file's status becomes `gmail_draft_created`, with the draft ID.

Acceptance: AC-G1–AC-G3 (§8). The rules from D8 still apply: no credentials in code, no substitute integration, never send.
