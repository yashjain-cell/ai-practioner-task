# actiontrack — Design & Implementation Plan

Implements [`requirements.md`](requirements.md) Rev 3. The checklist is in [`tasks.md`](tasks.md). R0–R8 are decided: R0 = draft files only, and R1–R8 use the defaults as written below.

## 1. Stack and layout

Python 3.13 (installed), **standard library only**. PyYAML is not installed. The two YAML shapes we need, the note front matter and a flat `people.yaml`, take a few lines to parse, so we don't add a dependency.

```
actiontrack.py          # whole tool: parse → resolve → merge → store → CLI
test_actiontrack.py     # unittest; run: python -m unittest. Synthetic notes are written inline to temp dirs
people.example.yaml     # placeholder emails (example.com); real people.yaml is git-ignored
pyproject.toml          # console script: actiontrack = actiontrack:main
.gitignore              # people.yaml, .actiontrack/
```
It is one module because it is one pipeline with about 400 lines. Split it only if a part grows on its own.

## 2. Pipeline

```
read file ─► decode (UTF-8 strict) ─► parse header ─► strip pseudo-task regions
          ─► extract mentions (checkbox | table | ACTION:) ─► parse owner/desc/due
          ─► resolve owner (attendees → initial) ─► resolve due (meeting date)
          ─► store.replace_mentions(file) ─► rebuild tasks from all mentions
```

### 2.1 File-level checks (in order, first match wins)
1. The extension is not `.md` → `NOT_MARKDOWN`, skip.
2. The file has 0 bytes, or contains only whitespace → `EMPTY_FILE`, skip.
3. It contains a NUL byte, or fails strict UTF-8 decoding → `UNDECODABLE`, skip.
4. Otherwise, parse it. A missing date gives `DATE_MISSING`, and missing attendees give `ATTENDEES_MISSING`. Both are warnings: the file's items are still ingested.

### 2.2 Header
- **Front matter**: the first line is `---`. Read `key: value` lines until the next `---`. `attendees: [a, b]` is split on commas.
- **Key lines**: `Date:` and `Attendees:` anywhere before the first `## ` heading.
- **Date parsing** accepts: ISO; `D Month YYYY`; `Weekday, D Month YYYY`. Anything else → `meeting_date=None`.

### 2.3 Pseudo-task regions (dropped before extraction)
Fenced code (from a ```` ``` ```` line to the next one), HTML comments (`<!--` … `-->`, which can span lines), and lines starting with `>`.

### 2.4 Mention extraction
| Format | Recognised by | `checked` |
|---|---|---|
| checkbox | `^\s*[-*+] \[( |x|X)\]\s*(.*)$` | `x`/`X` |
| table | a table header row with cells `Owner` and `Action`/`Task` (case-insensitive), then the `|---|` separator, then rows until a non-table line | false |
| action | `^ACTION:\s*(.+)$` | false |

Checkbox and `ACTION:` text is split as: `^(?P<owner>@?\w+(?: \w\.)?(?: & \w+)?(?: \(with \w+\))?)[: ]+to (?P<rest>.+)$`, and the `@handle:` form is also accepted (`@anita: audit …`). If the text has no owner prefix → `owner_raw=None`.

`rest` is split into description and due phrase at the **last** match of `(?:—\s*)?\b(by|on|due)\b\s+(.+)$`, or at a bare trailing `today|tomorrow`.

Empty checkbox text, or an empty Action cell → `MALFORMED_ENTRY`, skipped.

## 3. Task identity

```
desc_key  = normalise(description)      # lowercase, strip punctuation, drop a/an/the, collapse spaces
owner_key = resolved full name                      if owner resolved
          = "unmerged:" + source_file + ":" + line  otherwise (Unassigned / ambiguous / group)
task_id   = sha1(owner_key + "|" + desc_key).hexdigest()[:8]
```
- Unresolved owners carry their location in the key, so they **never merge or close across notes** (D2, E5, E7).
- The ID is a pure function of the note content, so it stays the same on re-ingest (FR-I6).
- Example: `send the revised quote to Acme` / `send revised quote to Acme` → `send revised quote to acme`, so both match.

## 4. Merge and completion

Tasks are **rebuilt** from all stored mentions each time ingest runs. The result doesn't depend on ingest order, and re-ingest gives the same store byte for byte.

For each group of mentions with the same `task_id`, sorted by (`meeting_date` with nulls last, `source_file`, `source_line`):
- `description`, `owner`: from the latest mention.
- `due`: from the latest mention whose `due` is not null (R2).
- `status = done` if any mention is `checked`, or if the ID is in `cli_done`. Otherwise `open`. Nothing reopens a done task (R6).
- `done_by`: `cli` if the ID is in `cli_done`, otherwise `note` if done, otherwise null.

**Confident match** = the same `task_id` (same resolved owner and the same `desc_key`). That is the **only** case where mentions merge or a `[x]` closes a task.

**Loose match** (reported, never acted on): the same resolved owner, with token-set Jaccard ≥ 0.5 on `desc_key`. The newer task gets `POSSIBLE_DUPLICATE: <other id>`. If the newer mention is checked, the run also reports `UNCONFIRMED_COMPLETION` against the open task.

**Ambiguous `[x]`**: the mention becomes its own (done) task, keyed by its location. If any open task held by one of its candidates has the same `desc_key`, report `UNCONFIRMED_COMPLETION`.

| Real pair | desc_key equal? | Outcome |
|---|---|---|
| "send the revised quote to Acme" / "send revised quote to Acme" | yes | merge |
| "estimate the SSO work" / "estimate SSO work" | yes | merge |
| "share first wireframes" / "share updated wireframes" | no (Jaccard 0.5) | 2 tasks + `POSSIBLE_DUPLICATE` |
| "review the schema mapping doc" ×2 (Rahul Verma both times) | yes | merge, due = 09-07 |

## 5. Owner resolution
1. `owner_raw` is None, or is `Someone`/`TBD` → `Unassigned` + `OWNER_MISSING`.
2. `Everyone`/`Team` → owner kept as written + `OWNER_GROUP` (R4).
3. Strip `@`. `A & B` → one mention per person (R4). `X (with Y)` → X owns it, and "(with Y)" stays in the description (R4).
4. A full name → used as is.
5. A first name → the attendees with that first name. If an initial is given (`Priya N.`), keep only the attendees whose surname starts with it. Exactly one left → resolved. Otherwise → ambiguous (`OWNER_AMBIGUOUS`, with the candidates taken from the attendees, or from all people seen in the notes when there are no attendees). No attendee list → ambiguous, even if the name is unique elsewhere (R3).

## 6. Due resolution
`resolve_due(phrase, meeting_date) -> (date|None, code|None)`. The rules are in requirements §7, with the R1 defaults:
- `EOD` = the meeting date.
- `from …`/`for …` → `DUE_UNSUPPORTED`.
- `next <weekday>`: Monday of the next ISO week + the weekday offset.
- A relative phrase with `meeting_date=None` → `DUE_NO_REFERENCE`.
- A day + month phrase with `meeting_date=None` → `DUE_NO_REFERENCE` (no year to use).

## 7. Store

`.actiontrack/tasks.json`, written atomically (write to a temp file, then `os.replace`) with sorted keys and `indent=2`:
```json
{ "version": 1,
  "mentions": [ {"source_file": "...", "source_line": 12, "meeting_date": "...", "format": "checkbox",
                 "checked": false, "owner_raw": "Priya N.", "owner": "Priya Nair", "candidates": [],
                 "text": "...", "description": "...", "due_raw": "by Friday", "due": "2026-09-11",
                 "warnings": []} ],
  "cli_done": ["a1b2c3d4"],
  "tasks": [ "...derived; written for readability and audit, rebuilt on every ingest/done" ] }
```
The store also keeps a `files` map: `{source_file: {meeting_date, attendees, warnings}}`. Owners are resolved when tasks are **rebuilt**, not when a file is parsed, so the candidate lists for notes without attendees can use attendees from every stored file. That keeps the result independent of ingest order.

`ingest <file>` **replaces** all mentions from that file. That keeps re-ingest idempotent, and edits to a note replace its old mentions instead of leaving stale ones. `cli_done` is never cleared by ingest.

## 8. CLI
`argparse` with subcommands `ingest | list | done | followup`. Global options are `--store`, `--people` and `--today` (the test-only date override). `list` prints a fixed-width table with these columns: `ID OWNER DUE STATUS DESCRIPTION SOURCES`.

## 9. Followup: two separate stages

### 9a. Draft file generation (MVP, in `actiontrack.py`, fully tested)
1. Select tasks with the same filter function as `list` (the default status is `overdue`). Group them by owner.
2. Leave out owners whose status is `unassigned`, `ambiguous` or `group`, and owners missing from `people.yaml`. Each one is reported (`OWNER_MISSING`, `OWNER_AMBIGUOUS`, `OWNER_GROUP`, `NO_EMAIL`).
3. Build one payload per owner:
   ```json
   {"status": "pending_gmail_draft", "to": "...", "owner": "Priya Shah",
    "subject": "Follow-up: 3 action item(s) from recent meetings",
    "body": "Hi Priya,\n\n- [id] description (due 2026-10-01) — file:line\n...\n\nThanks",
    "task_ids": ["..."], "generated_at": "2026-10-15T09:00:00"}
   ```
4. `--dry-run` prints the payloads. Otherwise each payload is written to `<store dir>/outbox/<YYYYMMDDTHHMMSSffffff>-<owner-slug>.json`, opened with mode `x` so an existing file can never be overwritten.
5. The final line says: `Wrote N draft file(s) to <dir> — not yet created in Gmail.`
6. `people.yaml` is parsed with the stdlib: flat `Full Name: email` lines, with `#` comments. If the file is missing → exit 1, pointing to `people.example.yaml`.
7. **No network:** the module imports no network library and makes no `send(` call. The tests check both, and also run `followup` with `socket.socket` patched to raise an error.

### 9b. Live Gmail draft creation (deferred, *not* in `actiontrack.py`)
This is a separate follow-up (requirements §12), run from a Claude Code session against the claude.ai Gmail MCP connector:
- Prerequisite: the connector is authenticated. As of 2026-10-15 it is installed but not authenticated, so its draft tool is **not yet verified**. Record the tool name and parameters here once checked: *TBD*.
- For each `outbox/*.json` with `status=pending_gmail_draft`: call the draft-creation tool only, then set `status=gmail_draft_created` and `gmail_draft_id`.
- If the connector is missing or unauthenticated → stop, with the message `Gmail MCP connector not available/authenticated. Authenticate the claude.ai Gmail connector, then retry. No other mail integration will be used.`
- The CLI never claims stage 9b happened.

## 10. Test plan (`test_actiontrack.py`, stdlib `unittest`)

Real-note tests copy `notes/` into a temporary directory first, then hash every file before and after.

| Area | Tests (each maps to an AC) |
|---|---|
| Safety | Notes are hash-identical after ingest (AC1). Running ingest twice gives a byte-identical store (AC3). |
| Malformed notes | Empty, binary and `.txt` files are skipped with codes. `untitled.md` and sales-pipeline are ingested with `DATE_MISSING`. Exit code is 2 (AC2). Fixtures: an empty checkbox and an empty table cell → `MALFORMED_ENTRY` (E13). |
| Pseudo-tasks | Blockquote, code fence and HTML comment are not ingested. The refund task is ingested once (AC4). |
| Formats | Table row and `ACTION:` line (AC5). `[X]`, `*`, `+` (AC6). YAML header (AC7). |
| Duplicate detection | Revised quote → 1 task, 3 mentions (AC8). Wireframes → 2 tasks + `POSSIBLE_DUPLICATE` (AC9). Schema doc merge and due date (AC10). |
| Completion propagation | Staging and renewal-call closed by later `[x]` (AC11). Ambiguous `[x]` doesn't close; loose-match `[x]` doesn't close (AC12 fixtures). |
| Relative dates | A table-driven test over every §7 row, plus fixtures for a same-day Friday, `today`, `next Tuesday`, `end of month` in February, `EOD`, `from` (AC15, AC16). |
| Ambiguous owners | Attendee resolution, initial resolution, both Priyas present, both Rahuls present, no attendee list (AC13, E8). |
| Missing data | `Unassigned` (AC14). Missing due date: open, not overdue, excluded by `--due-before` (AC18). `DUE_NO_REFERENCE` (AC16). |
| List / done | Overdue boundary at today (AC17). Owner filter ambiguity (AC19). Done survives re-ingest. Unknown ID (AC20). |
| Followup (9a) | Dry-run writes no files (AC21). One outbox file per eligible owner, and no overwrite on re-run (AC22). Skipped-owner reporting and exit 2 (AC23). Sockets patched to raise, static no-network scan, output wording (AC24). Missing `people.yaml` (AC25). |
| Gmail (9b) | Deferred. AC-G1–G3 are checked by hand when the follow-up task runs. Not part of `python -m unittest`. |

## 11. Implementation order
Parsing → dates → owners → merge → store/ingest → list/done → followup draft files. Gmail (9b) is a separate follow-up, not part of this order. Each step is written test-first. The step-by-step checklist is in `tasks.md`.
