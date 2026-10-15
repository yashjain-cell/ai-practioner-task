"""actiontrack: track meeting action items from Markdown notes.

Spec: requirements.md, design.md. Stdlib only. Never writes to the notes.
"""
import argparse
import calendar
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

EXIT_OK, EXIT_USER, EXIT_WARN = 0, 1, 2

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


class UserError(Exception):
    pass


# ---------------------------------------------------------------- dates

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
DAY_MONTH = re.compile(r"(?:([a-z]+)\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?")  # [Wed] 16 Sep [2026]
MONTH_DAY = re.compile(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?")                # Oct 3rd [2026]


def _lookup(word, names):
    """Index of the name that `word` spells or abbreviates (3+ letters: 'wed', 'sept'), else None."""
    w = word.lower().rstrip(".")
    if len(w) >= 3:
        for i, n in enumerate(names):
            if n.startswith(w):
                return i
    return None


def _day_month(p):
    """(month, day, year|None) for an explicit calendar date like '16 sep' or 'oct 3rd', else None."""
    m = DAY_MONTH.fullmatch(p)
    if m and (m[1] is None or _lookup(m[1], WEEKDAYS) is not None):
        month = _lookup(m[3], MONTHS)
        if month is not None:
            return month + 1, int(m[2]), int(m[4]) if m[4] else None
    m = MONTH_DAY.fullmatch(p)
    if m:
        month = _lookup(m[1], MONTHS)
        if month is not None:
            return month + 1, int(m[2]), int(m[3]) if m[3] else None
    return None


def _relative(p):
    """A function meeting_date -> due date for a supported relative phrase, else None (D6)."""
    day = dt.timedelta(days=1)
    if p in ("today", "eod", "end of day"):  # EOD = meeting date (R1)
        return lambda d: d
    if p in ("tomorrow", "eod tomorrow"):
        return lambda d: d + day
    if p in ("end of month", "end of the month"):
        return lambda d: d.replace(day=calendar.monthrange(d.year, d.month)[1])
    m = re.fullmatch(r"(next\s+)?([a-z]+)", p)
    wd = _lookup(m[2], WEEKDAYS) if m else None
    if wd is None:
        return None
    if m[1]:  # weekday of the following ISO week (R5)
        return lambda d: d - d.weekday() * day + (7 + wd) * day
    return lambda d: d + ((wd - d.weekday()) % 7) * day  # upcoming, including the meeting day


def resolve_due(phrase, meeting):
    """Resolve a due phrase against the meeting date -> (date | None, warning code | None)."""
    p = " ".join(phrase.lower().strip(" .,;").split())
    if ISO.fullmatch(p):
        try:
            return dt.date.fromisoformat(p), None
        except ValueError:
            return None, "DUE_UNSUPPORTED"
    if p.startswith(("from ", "for ")):  # start dates / scheduled-for dates are not deadlines (R1)
        return None, "DUE_UNSUPPORTED"
    explicit = _day_month(p)
    if explicit:
        month, day, year = explicit
        if year is None:
            if meeting is None:
                return None, "DUE_NO_REFERENCE"
            year = meeting.year
        try:
            return dt.date(year, month, day), None
        except ValueError:
            return None, "DUE_UNSUPPORTED"
    rel = _relative(p)
    if rel is None:
        return None, "DUE_UNSUPPORTED"
    if meeting is None:
        return None, "DUE_NO_REFERENCE"
    return rel(meeting), None


def parse_header_date(s):
    p = re.sub(r"^[a-z]+,\s*", "", " ".join(s.lower().split()))  # drop "monday, "
    if ISO.fullmatch(p):
        try:
            return dt.date.fromisoformat(p)
        except ValueError:
            return None
    dm = _day_month(p)
    if dm and dm[2]:
        try:
            return dt.date(dm[2], dm[0], dm[1])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- parsing

CHECKBOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\](.*)$")
ACTION = re.compile(r"^\s*ACTION:\s*(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_OWNER = r"@?[A-Za-z]+(?:\s+[A-Z]\.)?"
ITEM = re.compile(rf"^(?P<owners>{_OWNER}(?:\s*&\s*{_OWNER})*)(?P<helper>\s*\(with\s+[^)]+\))?\s+to\s+(?P<rest>.+)$")
HANDLE = re.compile(r"^@(?P<owners>[A-Za-z]+):\s*(?P<rest>.+)$")
DUE_MARKER = re.compile(r"\b(by|on|due|from|for)\s+")


def read_note(path):
    """-> (text, None) or (None, skip code). Never writes."""
    path = Path(path)
    if path.suffix.lower() != ".md":
        return None, "NOT_MARKDOWN"
    data = path.read_bytes()
    if not data.strip():
        return None, "EMPTY_FILE"
    if b"\0" in data:
        return None, "UNDECODABLE"
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "UNDECODABLE"


def _visible_lines(lines, start):
    """(line number, text) pairs outside code fences, HTML comments and blockquotes (§2.4)."""
    out, in_fence, in_comment = [], False, False
    for no, line in enumerate(lines[start:], start + 1):
        if in_comment:
            if "-->" not in line:
                continue
            line, in_comment = line.split("-->", 1)[1], False
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = re.sub(r"<!--.*?-->", "", line)
        if "<!--" in line:
            line, in_comment = line.split("<!--", 1)[0], True
        if line.lstrip().startswith(">"):
            continue
        out.append((no, line))
    return out


def _split_due(rest):
    """Split 'send X by Friday' -> ('send X', 'Friday'). Uses the last due marker that fits."""
    for m in reversed(list(DUE_MARKER.finditer(rest))):
        word, phrase = m[1], rest[m.end():].strip()
        first = phrase.split()[0] if phrase else ""
        if word == "on" and resolve_due(phrase, None)[1] == "DUE_UNSUPPORTED":
            continue  # "work on the dashboard" is not a date
        if word in ("from", "for"):
            if not (first[:1].isdigit() or _lookup(first, WEEKDAYS + MONTHS) is not None):
                continue  # "for accessibility"
            phrase = f"{word} {phrase}"  # keep the marker so resolve_due reports it unsupported (R1)
        return rest[:m.start()], phrase
    m = re.search(r"\s+(today|tomorrow)\s*$", rest)
    if m:
        return rest[:m.start()], m[1]
    return rest, None


def _owners(s):
    return [o.strip().lstrip("@") for o in s.split("&") if o.strip()]


def split_item(text):
    """'Priya N. to send X by Friday' -> (['Priya N.'], 'send X', 'Friday')."""
    text = text.strip()
    m = HANDLE.match(text) or ITEM.match(text)
    if not m:
        return [], text.rstrip(" —–-,;"), None
    desc, due_raw = _split_due(m["rest"])
    desc = desc.strip().rstrip(" —–-,;")
    helper = m.groupdict().get("helper")
    if helper:  # "Lena (with Priya)": first person owns it, helper kept in text (R4)
        desc = f"{desc} {helper.strip()}"
    return _owners(m["owners"]), desc, due_raw


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_note(text):
    """Parse one note -> {meeting_date, attendees, entries, warnings}. Pure; no I/O."""
    lines = text.splitlines()
    meta, start = {}, 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                start = i + 1
                break
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip()
        else:
            meta = {}  # unterminated front matter: treat as body
    if not start:
        for line in lines:
            if line.startswith("## "):
                break
            k, sep, v = line.partition(":")
            if sep and k.strip().lower() in ("date", "attendees"):
                meta.setdefault(k.strip().lower(), v.strip())

    meeting = parse_header_date(meta.get("date", ""))
    attendees = [a.strip() for a in meta.get("attendees", "").strip("[]").split(",") if a.strip()]
    warnings = []
    if meeting is None:
        warnings.append("DATE_MISSING")
    if not attendees:
        warnings.append("ATTENDEES_MISSING")

    entries = []

    def add(no, fmt, checked, owners, desc, due_raw, raw):
        if not desc:
            warnings.append("MALFORMED_ENTRY")
            return
        due, code = resolve_due(due_raw, meeting) if due_raw else (None, "DUE_MISSING")
        entries.append({"line": no, "format": fmt, "checked": checked, "owners_raw": owners,
                        "description": desc, "due_raw": due_raw, "due": due, "due_code": code,
                        "text": raw.strip()})

    vis = _visible_lines(lines, start)
    i = 0
    while i < len(vis):
        no, line = vis[i]
        if line.lstrip().startswith("|") and i + 1 < len(vis) and TABLE_SEP.match(vis[i + 1][1]):
            head = [c.lower() for c in _cells(line)]
            col = lambda *names: next((head.index(n) for n in names if n in head), None)
            o, a, d = col("owner"), col("action", "task"), col("due")
            i += 2
            while i < len(vis) and vis[i][1].lstrip().startswith("|"):
                if o is not None and a is not None:
                    c = _cells(vis[i][1])
                    get = lambda k: c[k] if k is not None and k < len(c) else ""
                    add(vis[i][0], "table", False, _owners(get(o)), get(a), get(d) or None, vis[i][1])
                i += 1
            continue
        m = CHECKBOX.match(line)
        if m:
            add(no, "checkbox", m[1] in "xX", *split_item(m[2]), line)
        elif m := ACTION.match(line):
            add(no, "action", False, *split_item(m[1]), line)
        i += 1
    return {"meeting_date": meeting, "attendees": attendees, "entries": entries, "warnings": warnings}


# ---------------------------------------------------------------- owners

UNASSIGNED_WORDS = {"someone", "tbd", "nobody"}
GROUP_WORDS = {"everyone", "team", "all"}


def resolve_owner(raw, attendees, known):
    """-> (owner, status, candidates). Never guesses: unclear names stay ambiguous (D4, D7, R3)."""
    if not raw or raw.lower() in UNASSIGNED_WORDS:
        return "Unassigned", "unassigned", []
    if raw.lower() in GROUP_WORDS:
        return raw, "group", []
    parts = raw.split()
    initial = parts[1][0].lower() if len(parts) == 2 and re.fullmatch(r"[A-Za-z]\.", parts[1]) else None
    if len(parts) >= 2 and initial is None:  # a full name is used as written
        return next((p for p in [*attendees, *known] if p.lower() == raw.lower()), raw), "resolved", []

    def match(pool):
        return sorted({p for p in pool if p.split()[0].lower() == parts[0].lower()
                       and (initial is None or p.split()[-1].lower().startswith(initial))})

    display = raw[0].upper() + raw[1:]
    if attendees:
        hits = match(attendees)
        if len(hits) == 1:
            return hits[0], "resolved", []
        return display, "ambiguous", hits or match(known)
    return display, "ambiguous", match(known)  # no attendee list: never resolve (R3)


# ---------------------------------------------------------------- scan + merge

STOPWORDS = {"a", "an", "the"}


def normalise(s):
    return " ".join(w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOPWORDS)


def _rel(p):
    try:
        return Path(os.path.relpath(p)).as_posix()
    except ValueError:  # different drive on Windows
        return Path(p).resolve().as_posix()


def scan(path):
    """Parse a note file or directory -> (files, mentions, report). Read-only."""
    path = Path(path)
    if not path.exists():
        raise UserError(f"no such file or directory: {path}")
    paths = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    files, mentions, report = {}, [], []
    for p in paths:
        rel = _rel(p)
        text, skip = read_note(p)
        if skip:
            files[rel] = {"meeting_date": None, "attendees": [], "warnings": [skip], "skipped": True}
            report.append((rel, skip))
            continue
        n = parse_note(text)
        md = n["meeting_date"].isoformat() if n["meeting_date"] else None
        files[rel] = {"meeting_date": md, "attendees": n["attendees"], "warnings": n["warnings"]}
        report += [(rel, w) for w in n["warnings"]]
        for e in n["entries"]:
            for owner in e["owners_raw"] or [None]:  # "Anita & Rahul" -> one mention each (R4)
                mentions.append({
                    "source_file": rel, "source_line": e["line"], "meeting_date": md,
                    "format": e["format"], "checked": e["checked"], "owner_raw": owner,
                    "description": e["description"], "due_raw": e["due_raw"],
                    "due": e["due"].isoformat() if e["due"] else None, "due_code": e["due_code"],
                    "text": e["text"]})
    return files, mentions, report


def _mention_order(m):
    return m["meeting_date"] or "9999-99-99", m["source_file"], m["source_line"], m["owner_raw"] or ""


def _jaccard(a, b):
    a, b = set(a.split()), set(b.split())
    return len(a & b) / len(a | b) if a | b else 0.0


def build_tasks(files, mentions, cli_done):
    """Derive tasks from all mentions. Pure and order-independent (design §4)."""
    known = sorted({a for f in files.values() for a in f["attendees"]})
    groups = {}
    for m in mentions:
        owner, status, cands = resolve_owner(m["owner_raw"], files[m["source_file"]]["attendees"], known)
        # Only resolved owners merge across notes; anything unclear is pinned to its own line.
        key = owner if status == "resolved" else f"unmerged:{m['source_file']}:{m['source_line']}:{m['owner_raw']}"
        dkey = normalise(m["description"])
        tid = hashlib.sha1(f"{key}|{dkey}".encode()).hexdigest()[:8]
        groups.setdefault(tid, []).append((m, owner, status, cands, dkey))

    tasks = []
    for tid, ms in groups.items():
        ms.sort(key=lambda x: _mention_order(x[0]))
        last, owner, status, cands, dkey = ms[-1]
        due = next((x[0]["due"] for x in reversed(ms) if x[0]["due"]), None)  # R2
        warnings = {"unassigned": {"OWNER_MISSING"}, "ambiguous": {"OWNER_AMBIGUOUS"},
                    "group": {"OWNER_GROUP"}}.get(status, set())
        if due is None:
            warnings.add(next((x[0]["due_code"] for x in reversed(ms) if x[0]["due_raw"]), "DUE_MISSING"))
        checked = any(x[0]["checked"] for x in ms)
        done = checked or tid in cli_done
        tasks.append({
            "id": tid, "owner": owner, "owner_status": status, "owner_candidates": cands,
            "description": last["description"], "due": due,
            "status": "done" if done else "open",
            "done_by": "cli" if tid in cli_done else "note" if checked else None,
            "mentions": [{k: x[0][k] for k in ("source_file", "source_line", "meeting_date", "format",
                                                "checked", "owner_raw", "due_raw", "due")} for x in ms],
            "warnings": warnings, "_dkey": dkey, "_checked": checked})
    tasks.sort(key=lambda t: (_mention_order(t["mentions"][0]), t["id"]))

    # Loose matches are reported, never merged or closed (D2).
    # ponytail: O(n^2) per owner; fine for hundreds of tasks, bucket by token if it ever isn't.
    for i, older in enumerate(tasks):
        for newer in tasks[i + 1:]:
            if older["owner_status"] == newer["owner_status"] == "resolved" and older["owner"] == newer["owner"] \
                    and older["_dkey"] != newer["_dkey"] and _jaccard(older["_dkey"], newer["_dkey"]) >= 0.5:
                newer["warnings"].add(f"POSSIBLE_DUPLICATE:{older['id']}")
                if newer["_checked"] and older["status"] == "open":
                    older["warnings"].add(f"UNCONFIRMED_COMPLETION:{newer['id']}")
    for amb in (t for t in tasks if t["owner_status"] == "ambiguous" and t["_checked"]):
        for t in tasks:
            if t["status"] == "open" and t["owner"] in amb["owner_candidates"] and t["_dkey"] == amb["_dkey"]:
                t["warnings"].add(f"UNCONFIRMED_COMPLETION:{amb['id']}")

    for t in tasks:
        del t["_dkey"], t["_checked"]
        t["warnings"] = sorted(t["warnings"])
    return tasks


# ---------------------------------------------------------------- store

def load_store(path):
    p = Path(path)
    if not p.exists():
        return {"version": 1, "files": {}, "mentions": [], "cli_done": [], "tasks": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise UserError(f"store {p} is unreadable ({e}); fix or move it, it was not modified")


def save_store(path, store):
    """Rebuild tasks and write atomically, with stable formatting (byte-identical on re-run)."""
    store["cli_done"] = sorted(set(store["cli_done"]))
    store["tasks"] = build_tasks(store["files"], store["mentions"], set(store["cli_done"]))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- selection

def is_overdue(t, today):
    return t["status"] == "open" and t["due"] is not None and t["due"] < today.isoformat()


def _owner_matches(t, q):
    o = t["owner"].lower()
    return o == q or (" " not in q and o.split()[0] == q)


def select(tasks, owner, status, due_before, today):
    """Shared by list and followup -> (tasks, notice | None)."""
    q = owner.strip().lower() if owner else None
    out = [t for t in tasks
           if (q is None or _owner_matches(t, q))
           and (status is None
                or (status == "overdue" and is_overdue(t, today))
                or (status in ("open", "done") and t["status"] == status))
           and (due_before is None or (t["due"] is not None and t["due"] < due_before.isoformat()))]
    notice = None
    if q and " " not in q:
        people = sorted({t["owner"] for t in tasks if t["owner_status"] == "resolved"
                         and t["owner"].lower().split()[0] == q})
        if len(people) > 1:
            notice = f"note: '{owner}' matches several people ({', '.join(people)}); showing all of them"
    return out, notice


# ---------------------------------------------------------------- commands

def cmd_ingest(args, today):
    root, store_path = Path(args.path).resolve(), Path(args.store).resolve()
    if store_path == root or root in store_path.parents:
        raise UserError("the store must not be inside the notes directory (notes are never written)")
    store = load_store(args.store)
    files, mentions, report = scan(args.path)
    before = {t["id"]: t["status"] for t in store["tasks"]}
    store["files"] = {k: v for k, v in store["files"].items() if k not in files} | files
    store["mentions"] = sorted([m for m in store["mentions"] if m["source_file"] not in files] + mentions,
                               key=_mention_order)
    save_store(args.store, store)

    tasks = store["tasks"]
    touched = [t for t in tasks if any(m["source_file"] in files for m in t["mentions"])]
    for rel, code in report:
        print(f"  {code:<20} {rel}")
    counts = {}
    for t in touched:
        for w in t["warnings"]:
            counts[w.split(":")[0]] = counts.get(w.split(":")[0], 0) + 1
    skipped = sum(1 for f in files.values() if f.get("skipped"))
    created = sum(1 for t in tasks if t["id"] not in before)
    closed = sum(1 for t in tasks if before.get(t["id"]) == "open" and t["status"] == "done")
    print(f"Scanned {len(files)} file(s), skipped {skipped}; {len(mentions)} mention(s) -> "
          f"{len(touched)} task(s) from these files ({created} new, {closed} newly done); "
          f"store holds {len(tasks)} tasks.")
    for code in sorted(counts):
        print(f"  {counts[code]:>3} task(s) with {code}")
    return EXIT_WARN if report or counts else EXIT_OK


def _sources(t):
    return ", ".join(f"{Path(m['source_file']).name}:{m['source_line']}" for m in t["mentions"])


def cmd_list(args, today):
    tasks, notice = select(load_store(args.store)["tasks"], args.owner, args.status, args.due_before, today)
    if notice:
        print(notice, file=sys.stderr)
    if not tasks:
        print("No matching tasks.")
        return EXIT_OK
    print(f"{'ID':<8}  {'OWNER':<14}  {'DUE':<10}  {'STATUS':<7}  {'DESCRIPTION':<48}  SOURCES")
    for t in tasks:
        owner = t["owner"] + ("?" if t["owner_status"] == "ambiguous" else "")
        status = "overdue" if is_overdue(t, today) else t["status"]
        print(f"{t['id']:<8}  {owner:<14}  {t['due'] or '-':<10}  {status:<7}  {t['description']:<48}  {_sources(t)}")
    return EXIT_OK


def cmd_done(args, today):
    store = load_store(args.store)
    by_id = {t["id"]: t for t in store["tasks"]}
    unknown = [i for i in args.ids if i not in by_id]
    if unknown:
        raise UserError(f"unknown task id(s): {', '.join(unknown)}; nothing changed")
    changed = False
    for i in args.ids:
        if by_id[i]["status"] == "done":
            print(f"{i} already done")
        else:
            store["cli_done"].append(i)
            changed = True
            print(f"{i} marked done: {by_id[i]['description']}")
    if changed:
        save_store(args.store, store)
    return EXIT_OK


# ---------------------------------------------------------------- followup (draft files only)
# The MVP never talks to Gmail or the network. Outbox files are handed to a separate,
# deferred step that creates Gmail drafts via the MCP connector (design.md §9b).

SKIP_REASONS = {"unassigned": "OWNER_MISSING", "ambiguous": "OWNER_AMBIGUOUS", "group": "OWNER_GROUP"}


def load_people(path):
    """Flat 'Full Name: email' lines (a YAML subset), '#' comments."""
    p = Path(path)
    if not p.exists():
        raise UserError(f"{p} not found; copy people.example.yaml to {p} and fill in real addresses")
    people = {}
    for no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, email = line.partition(":")
        email = email.strip().strip("'\"")
        if not sep or "@" not in email:
            raise UserError(f"{p}:{no}: expected 'Full Name: email'")
        people[name.strip().strip("'\"")] = email
    return people


def _payload(owner, email, tasks, today, now):
    lines = [f"Hi {owner.split()[0]},", "", "Following up on your action items from recent meetings:", ""]
    for t in tasks:
        due = (t["due"] or "no date set") + (", overdue" if is_overdue(t, today) else "")
        lines.append(f"- [{t['id']}] {t['description']} (due {due}) — {_sources(t)}")
    lines += ["", "Could you reply with a status update or a new date?", "", "Thanks"]
    return {"status": "pending_gmail_draft", "to": email, "owner": owner,
            "subject": f"Follow-up: {len(tasks)} action item(s) from recent meetings",
            "body": "\n".join(lines), "task_ids": [t["id"] for t in tasks],
            "generated_at": now.isoformat(timespec="seconds")}


def _write_new(outbox, stem, payload):
    """Write to a fresh file; never overwrite (FR-F8)."""
    for n in range(1000):
        path = outbox / f"{stem}{f'-{n}' if n else ''}.json"
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            return path
        except FileExistsError:
            continue
    raise UserError(f"could not find a free file name in {outbox}")


def cmd_followup(args, today):
    tasks, notice = select(load_store(args.store)["tasks"], args.owner, args.status, args.due_before, today)
    if notice:
        print(notice, file=sys.stderr)
    people = load_people(args.people)
    if not tasks:
        print("No matching tasks; no draft files written.")
        return EXIT_OK

    by_owner, skipped = {}, []
    for t in tasks:
        reason = SKIP_REASONS.get(t["owner_status"]) or (None if t["owner"] in people else "NO_EMAIL")
        if reason:
            skipped.append((reason, t))
        else:
            by_owner.setdefault(t["owner"], []).append(t)

    for reason, t in skipped:
        who = t["owner"] + (f" (could be {', '.join(t['owner_candidates'])})" if t["owner_candidates"] else "")
        print(f"  {reason:<16} {t['id']}  {who}: {t['description']}")
    for owner in sorted({t["owner"] for r, t in skipped if r == "NO_EMAIL"}):
        print(f"  follow-up cannot proceed for {owner}: no email address in {args.people}")

    now = dt.datetime.now()
    payloads = [_payload(o, people[o], ts, today, now) for o, ts in sorted(by_owner.items())]
    if args.dry_run:
        for p in payloads:
            print(json.dumps(p, indent=2, ensure_ascii=False))
        print(f"Dry run: {len(payloads)} draft(s) prepared; no files written, nothing sent.")
    else:
        outbox = Path(args.store).parent / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%S%f")
        for p in payloads:
            slug = re.sub(r"[^a-z0-9]+", "-", p["owner"].lower()).strip("-")
            print(f"  wrote {_write_new(outbox, f'{stamp}-{slug}', p).as_posix()}")
        print(f"Wrote {len(payloads)} draft file(s) to {outbox.as_posix()} — not yet created in Gmail.")
    return EXIT_WARN if skipped else EXIT_OK


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse exits 2; spec reserves 2 for warnings
        self.print_usage(sys.stderr)
        print(f"actiontrack: error: {message}", file=sys.stderr)
        sys.exit(EXIT_USER)


def iso_date(s):
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {s!r}, expected YYYY-MM-DD")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=".actiontrack/tasks.json")
    common.add_argument("--people", default="people.yaml")
    common.add_argument("--today", type=iso_date, help="test-only override of the local date")

    p = _Parser(prog="actiontrack", description="Track meeting action items.")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    s = sub.add_parser("ingest", parents=[common], help="extract action items from notes")
    s.add_argument("path")

    for name in ("list", "followup"):
        s = sub.add_parser(name, parents=[common])
        s.add_argument("--owner")
        statuses = ["open", "done", "overdue"] if name == "list" else ["open", "overdue"]
        s.add_argument("--status", choices=statuses, default=None if name == "list" else "overdue")
        s.add_argument("--due-before", type=iso_date)
        if name == "followup":
            s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("done", parents=[common], help="mark tasks done")
    s.add_argument("ids", nargs="+")
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # note text may not fit a Windows console code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    today = args.today or dt.date.today()
    try:
        return COMMANDS[args.cmd](args, today)
    except UserError as e:
        print(f"actiontrack: error: {e}", file=sys.stderr)
        return EXIT_USER


COMMANDS = {"ingest": cmd_ingest, "list": cmd_list, "done": cmd_done, "followup": cmd_followup}

if __name__ == "__main__":
    sys.exit(main())
