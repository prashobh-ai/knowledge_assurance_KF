"""Ingestion gate: detect -> integrity -> readability -> classify -> de-duplicate -> validate -> decide.

Checks run cheapest first. Everything here is a rule or a statistical check; the hook for an
LLM judge is `llm_checks`, left empty in this PoC so it runs at zero cost.
"""
import datetime as dt
import hashlib
import json
import os
import random
import re
import zlib

from .store import DB_PATH, connect
from .text import tokens

ALLOWED_LICENCES = {"MIT", "Apache-2.0", "BSD-3-Clause"}
APPROVED_ISSUERS = {"Claims Policy Office", "Compliance"}
SECRET_RE = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]")
JIRA_KEY_RE = re.compile(r"\b[A-Z]{2,5}-\d+\b")
LABEL_RE = re.compile(r"^[a-z]+(-[a-z]+)*$")
CLAIM_RE = re.compile(r"^[-*]\s*([A-Za-z][A-Za-z ]{2,40}):\s*(\d[\w .%]*)$", re.M)
AUTHORITY = {"policy": 1.0, "prd": 0.9, "jira_story": 0.8, "code_repo": 0.8, "meeting": 0.6, "document": 0.5}
NEAR_DUP = 0.8

_rng = random.Random(42)
_P = 4294967311
_HASHES = [(_rng.randrange(1, _P), _rng.randrange(0, _P)) for _ in range(64)]


def minhash(text):
    words = tokens(text)
    shingles = {" ".join(words[i:i + 3]) for i in range(max(1, len(words) - 2))}
    crcs = [zlib.crc32(s.encode()) for s in shingles] or [0]
    return [min((a * c + b) % _P for c in crcs) for a, b in _HASHES]


def jaccard(sig_a, sig_b):
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)


def classify(path, text):
    p = path.replace("\\", "/")
    for key, ktype in (("/jira/", "jira_story"), ("/prd/", "prd"), ("/policies/", "policy"),
                       ("/repo/", "code_repo"), ("/meetings/", "meeting")):
        if key in p:
            return ktype
    low = text.lower()
    if "acceptance criteria" in low:
        return "jira_story"
    if "effective date" in low:
        return "policy"
    return "document"


def readable_text(raw, path):
    """Return (text, problem). Problem is None when the file is intact and readable."""
    if not raw.strip():
        return "", "empty file"
    if path.lower().endswith(".pdf") and not raw.startswith(b"%PDF"):
        return "", "file extension says PDF but content is not a PDF"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "", "not valid text (binary or damaged)"
    if path.endswith(".json"):
        try:
            d = json.loads(text)
        except ValueError:
            return "", "invalid JSON"
        text = "\n\n".join([f"{d.get('key', '')} {d.get('summary', '')}", d.get("description", ""),
                            "Acceptance criteria:\n" + "\n".join("- " + c for c in d.get("acceptance_criteria", []))])
    return text, None


def readability(text):
    words = re.findall(r"\S+", text)
    if len(words) < 20:
        return 0.0
    good = sum(1 for w in words if re.fullmatch(r"[A-Za-z][a-z]+[.,:;]?|[A-Z]{2,6}-?\d*|\d[\d.,:%/-]*|[-*#\[\]()]+", w))
    return good / len(words)


def check(name, ok, level="normal", detail=""):
    return {"check": name, "pass": bool(ok), "level": level, "detail": detail}


def profile_checks(ktype, entry, text, raw_json):
    c = []
    if ktype == "jira_story":
        ac = raw_json.get("acceptance_criteria") or []
        c.append(check("acceptance criteria present", len(ac) > 0, "mandatory"))
        c.append(check("epic and fix version linked", bool(raw_json.get("epic")) and bool(raw_json.get("fix_version"))))
        labels = raw_json.get("labels") or []
        c.append(check("labels follow convention", bool(labels) and all(LABEL_RE.match(x) for x in labels), detail=",".join(labels)))
    elif ktype == "code_repo":
        m = SECRET_RE.search(text)
        c.append(check("no secrets in content", m is None, "gate", detail="secret-like assignment found" if m else ""))
        c.append(check("licence allowed", entry.get("licence") in ALLOWED_LICENCES, detail=str(entry.get("licence"))))
        c.append(check("linked to a Jira key", bool(JIRA_KEY_RE.search(text))))
    elif ktype == "policy":
        c.append(check("issuer in approved registry", entry.get("issuer") in APPROVED_ISSUERS, "gate", detail=str(entry.get("issuer"))))
        c.append(check("effective date present", bool(entry.get("valid_from")), "mandatory"))
        rel_ok = (not entry.get("relation")) or bool(entry.get("relates_to"))
        c.append(check("amend / supersede link set", rel_ok))
    elif ktype == "prd":
        c.append(check("status approved", "status: approved" in text.lower(), "mandatory"))
        c.append(check("version stated", bool(re.search(r"(?i)version:\s*\S+", text))))
    elif ktype == "meeting":
        c.append(check("timestamps present", bool(re.search(r"\[\d{1,2}:\d{2}\]", text))))
        c.append(check("meeting date present", bool(entry.get("valid_from"))))
    c.append(check("owner registered", bool(entry.get("owner")), "mandatory"))
    return c


def claims(text):
    return {k.strip().lower(): v.strip().lower() for k, v in CLAIM_RE.findall(text)}


def contradictions(con, entry, text):
    mine, found = claims(text), []
    if not mine or not entry.get("topic"):
        return found
    rows = con.execute("SELECT id FROM assets WHERE topic=? AND outcome='accept' AND valid_to IS NULL AND id!=?",
                       (entry["topic"], entry.get("relates_to") or "")).fetchall()
    for r in rows:
        other = "\n".join(x["text"].split("] ", 1)[-1] for x in con.execute("SELECT text FROM chunks WHERE asset_id=?", (r["id"],)))
        for k, v in claims(other).items():
            if k in mine and mine[k] != v:
                found.append(f"{k}: '{mine[k]}' here vs '{v}' in {r['id']}")
    return found


def trust_score(checks, ktype, entry, today):
    passed = sum(1 for c in checks if c["pass"]) / max(1, len(checks))
    reviewed = entry.get("reviewed")
    fresh = 1.0 if reviewed and (today - dt.date.fromisoformat(reviewed)).days <= 365 else 0.6
    return round(0.5 * passed + 0.2 * AUTHORITY.get(ktype, 0.5) + 0.3 * fresh, 2)


def chunk(title, text):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [f"[{title}] {p}" for p in paras]   # every chunk carries its document context


def ingest(registry_path="data/registry.json", db_path=None, today=None, fresh=True):
    today = today or dt.date.today()
    con = connect(db_path or DB_PATH, fresh=fresh)
    base = os.path.dirname(registry_path)
    results, sigs = [], {}
    with open(registry_path, encoding="utf-8") as fh:
        registry = json.load(fh)
    for entry in registry:
        path = os.path.join(base, entry["path"])
        with open(path, "rb") as fh:
            raw = fh.read()
        sha = hashlib.sha256(raw).hexdigest()
        if con.execute("SELECT 1 FROM assets WHERE id=? AND sha=?", (entry["id"], sha)).fetchone():
            results.append({"id": entry["id"], "outcome": "unchanged"})
            continue
        evidence, outcome, ktype, trust, text = [], None, "unknown", 0.0, ""
        text, problem = readable_text(raw, path)
        evidence.append(check("file integrity", problem is None, "gate", problem or ""))
        if problem:
            outcome = "reject"
        if not outcome:
            score = readability(text)
            evidence.append(check("readability", score >= 0.6, "mandatory", f"score {score:.2f}"))
            ktype = classify(path, text)
            dup = con.execute("SELECT id FROM assets WHERE sha=?", (sha,)).fetchone()
            sig = minhash(text)
            near = max(((jaccard(sig, s), i) for i, s in sigs.items() if i != entry.get('relates_to')), default=(0, None))  # declared versions are expected to look alike
            evidence.append(check("not an exact duplicate", dup is None, "gate", f"same as {dup['id']}" if dup else ""))
            evidence.append(check("not a near-duplicate", near[0] < NEAR_DUP, "mandatory", f"{near[0]:.2f} similar to {near[1]}" if near[1] else ""))
            raw_json = json.loads(raw) if path.endswith(".json") else {}
            evidence += profile_checks(ktype, entry, text, raw_json)
            conflicts = contradictions(con, entry, text)
            evidence.append(check("no conflict with current knowledge", not conflicts, "review", "; ".join(conflicts)))
            failed = {c["level"] for c in evidence if not c["pass"]}
            if dup is not None:
                outcome = "reject"
            elif "gate" in failed:
                outcome = "quarantine"
            elif "review" in failed:
                outcome = "human_review"
            elif "mandatory" in failed:
                outcome = "reference_only"
            else:
                outcome = "accept"
            trust = 0.0 if outcome in ("reject", "quarantine") else trust_score(evidence, ktype, entry, today)
            if outcome == "accept":
                sigs[entry["id"]] = sig
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        con.execute("INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (entry["id"], entry["path"], entry.get("title", entry["id"]), ktype, entry.get("owner"), entry.get("topic"),
                     sha, outcome, trust, json.dumps(entry.get("acl", ["employee"])), entry.get("valid_from"), entry.get("valid_to"),
                     now, None, entry.get("relation"), entry.get("relates_to"), json.dumps(evidence)))
        if outcome in ("accept", "reference_only"):
            for seq, ch in enumerate(chunk(entry.get("title", entry["id"]), text)):
                cur = con.execute("INSERT INTO chunks(asset_id, seq, text) VALUES(?,?,?)", (entry["id"], seq, ch))
                con.execute("INSERT INTO fts(rowid, text) VALUES(?,?)", (cur.lastrowid, ch))
        if outcome == "accept":
            if entry.get("relation") == "supersedes" and entry.get("relates_to"):
                con.execute("UPDATE assets SET valid_to=?, superseded_at=? WHERE id=?", (entry.get("valid_from"), now, entry["relates_to"]))
            if entry.get("relation"):
                con.execute("INSERT INTO links VALUES(?,?,?)", (entry["id"], entry["relates_to"], entry["relation"]))
            for key in set(JIRA_KEY_RE.findall(text)) - {entry["id"]}:
                con.execute("INSERT INTO links VALUES(?,?,?)", (entry["id"], key, "mentions"))
        results.append({"id": entry["id"], "type": ktype, "outcome": outcome, "trust": trust,
                        "failed": [f"{c['check']}" + (f" ({c['detail']})" if c["detail"] else "") for c in evidence if not c["pass"]]})
    con.commit()
    con.close()
    return results
