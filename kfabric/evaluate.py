"""Golden-set regression with failure attribution (source / retrieval / generation / policy)."""
import json

from .retrieve import ask
from .store import DB_PATH, connect


def attribute(item, res, accepted):
    """Return None when the case passes, otherwise the layer that caused the failure."""
    expect = item["expect"]
    answered = res["decision"] != "abstain"
    if expect == "abstain":
        return "policy" if answered else None
    if any(x in res["citations"] for x in item.get("must_not_cite", [])):
        return "policy"
    sources = item.get("expect_sources", [])
    if sources and not any(s in accepted for s in sources):
        return "source"
    if sources and not any(s in res["retrieved"] for s in sources):
        return "retrieval"
    if sources and not any(s in res["citations"] for s in sources):
        return "generation"
    if not answered or res["decision"] != expect:
        return "policy"
    return None


def run(golden_path="data/golden.json", db_path=None):
    con = connect(db_path or DB_PATH)
    rows = con.execute("SELECT id, outcome, trust FROM assets").fetchall()
    con.close()
    accepted = {r["id"] for r in rows if r["outcome"] == "accept"}
    outcomes = {}
    for r in rows:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    cases, failures = [], {}
    with open(golden_path, encoding="utf-8") as fh:
        golden = json.load(fh)
    for item in golden:
        res = ask(item["question"], roles=item.get("roles", ["employee"]), as_of=item.get("as_of"),
                  action=item.get("action", "inform"), db_path=db_path)
        layer = attribute(item, res, accepted)
        if layer:
            failures[layer] = failures.get(layer, 0) + 1
        cases.append({"id": item["id"], "question": item["question"], "expected": item["expect"], "decision": res["decision"],
                      "citations": res["citations"], "missing": res["missing"], "trace": res["trace"],
                      "result": "pass" if not layer else f"fail: {layer}"})
    n = len(cases)
    passed = sum(1 for c in cases if c["result"] == "pass")
    trusts = [r["trust"] for r in rows if r["outcome"] == "accept"]
    return {"ingestion": {"assets": len(rows), "outcomes": outcomes,
                          "mean_trust_of_accepted": round(sum(trusts) / max(1, len(trusts)), 2)},
            "golden_set": {"cases": n, "passed": passed, "pass_rate": round(passed / max(1, n), 2), "failures_by_layer": failures},
            "cases": cases}
