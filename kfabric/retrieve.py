"""Retrieval and answer flow: pre-filter -> hybrid search -> graph expansion -> rerank by trust
-> sufficiency check -> extractive answer with a citation per sentence -> answer / flag gap / abstain.

The answer is extractive (sentences copied from approved chunks), so groundedness holds by
construction. A generative model can be plugged in behind `grounded()` later.
"""
import datetime as dt
import json
import math
from collections import Counter

from .ingest import AUTHORITY
from .store import DB_PATH, connect
from .text import sentences, tokens

TRUST_FLOOR = {"inform": 0.5, "recommend": 0.6, "decide": 0.7, "act": 0.8}
MIN_AUTHORITY = {"inform": 0.0, "recommend": 0.0, "decide": 0.8, "act": 0.9}   # e.g. meeting notes cannot drive an action
RRF_K = 60


def _eligible(con, roles, as_of, action):
    floor = TRUST_FLOOR[action]
    rows = con.execute("SELECT * FROM assets WHERE outcome='accept' AND trust>=?", (floor,)).fetchall()
    ok = {}
    for a in rows:
        if AUTHORITY.get(a["ktype"], 0.5) < MIN_AUTHORITY[action]:
            continue
        if not set(json.loads(a["acl"])) & set(roles):
            continue                                   # access is checked before search, never after
        if a["valid_from"] and a["valid_from"] > as_of:
            continue
        if a["valid_to"] and a["valid_to"] <= as_of:
            continue                                   # superseded or expired on the date asked
        ok[a["id"]] = a
    return ok


def _tfidf_rank(q_tokens, chunks):
    docs = {cid: Counter(tokens(text)) for cid, text in chunks.items()}
    df = Counter(t for d in docs.values() for t in d)
    n = max(1, len(docs))
    idf = {t: math.log(1 + n / df[t]) for t in df}
    qv = {t: idf.get(t, 0.0) for t in q_tokens}
    scores = {}
    for cid, d in docs.items():
        dot = sum(qv[t] * d[t] * idf[t] for t in qv if t in d)
        norm = math.sqrt(sum((c * idf[t]) ** 2 for t, c in d.items())) or 1.0
        if dot > 0:
            scores[cid] = dot / norm
    return sorted(scores, key=scores.get, reverse=True)[:10]


def grounded(sentence, evidence_text):
    st = set(tokens(sentence))
    return bool(st) and len(st & set(tokens(evidence_text))) / len(st) >= 0.8


def ask(question, roles=("employee",), as_of=None, action="inform", db_path=None):
    as_of = as_of or dt.date.today().isoformat()
    con = connect(db_path or DB_PATH)
    total = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assets = _eligible(con, roles, as_of, action)
    chunks = {}
    for aid in assets:
        for r in con.execute("SELECT id, text FROM chunks WHERE asset_id=?", (aid,)):
            chunks[r["id"]] = (aid, r["text"])
    trace = {"chunks_total": total, "after_prefilter": len(chunks)}
    q = list(dict.fromkeys(tokens(question)))
    result = {"question": question, "as_of": as_of, "roles": list(roles), "action": action, "trace": trace,
              "answer": [], "citations": [], "retrieved": [], "missing": q, "decision": "abstain"}
    if not chunks or not q:
        con.close()
        return result
    fts_q = " OR ".join(f'"{t}"' for t in q)
    bm25 = [r["rowid"] for r in con.execute("SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 40", (fts_q,))
            if r["rowid"] in chunks][:10]
    vec = _tfidf_rank(q, {cid: t for cid, (_, t) in chunks.items()})
    score = Counter()
    for ranking in (bm25, vec):                        # reciprocal rank fusion of keyword and vector lists
        for rank, cid in enumerate(ranking):
            score[cid] += 1.0 / (RRF_K + rank + 1)
    top_assets = list(dict.fromkeys(chunks[c][0] for c, _ in score.most_common(3)))
    for aid in top_assets:                             # graph expansion: follow links to approved assets only
        for link in con.execute("SELECT dst FROM links WHERE src=? UNION SELECT src FROM links WHERE dst=?", (aid, aid)):
            for cid, (owner, _) in chunks.items():
                if owner == link[0] and cid not in score:
                    score[cid] = 0.5 / (RRF_K + 10)
    trace["after_search_and_expand"] = len(score)
    ranked = sorted(score, key=lambda c: score[c] * (0.5 + 0.5 * assets[chunks[c][0]]["trust"]), reverse=True)[:5]
    trace["after_rerank"] = len(ranked)
    con.close()
    result["retrieved"] = list(dict.fromkeys(chunks[c][0] for c in ranked))
    evidence = " ".join(chunks[c][1] for c in ranked)
    ev_tokens = set(tokens(evidence))
    missing = [t for t in q if t not in ev_tokens]
    coverage = 1 - len(missing) / len(q)
    result.update(missing=missing, coverage=round(coverage, 2))
    if coverage < 0.4:
        return result                                  # not enough evidence: abstain instead of guessing
    picked = []
    for c in ranked:
        aid, text = chunks[c]
        for s in sentences(text.split("] ", 1)[-1]):
            overlap = len(set(tokens(s)) & set(q))
            if overlap and grounded(s, text):
                picked.append((overlap, s, aid))
    picked.sort(key=lambda x: -x[0])
    seen = set()
    for _, s, aid in picked:
        if s not in seen and len(result["answer"]) < 3:
            seen.add(s)
            result["answer"].append(f"{s} [{aid}]")
            if aid not in result["citations"]:
                result["citations"].append(aid)
    if not result["answer"]:
        return result
    result["decision"] = "answer" if coverage >= 0.7 else "answer_with_gap"
    return result
