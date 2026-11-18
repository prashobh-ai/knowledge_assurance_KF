"""Command line: python -m kfabric demo | ingest | ask "question" | eval"""
import argparse
import json
import os
import sys

from .evaluate import run
from .ingest import ingest
from .retrieve import ask


def _print_ingest(results):
    print(f"{'asset':38} {'type':11} {'outcome':15} trust  failed checks")
    for r in results:
        print(f"{r['id']:38} {r.get('type', ''):11} {r['outcome']:15} {r.get('trust', 0):<5}  {'; '.join(r.get('failed', []))}")


def _summary(report):
    lines = ["## Knowledge Fabric PoC run", "", "| Ingestion outcome | Assets |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(report["ingestion"]["outcomes"].items())]
    g = report["golden_set"]
    lines += ["", f"**Golden set:** {g['passed']} of {g['cases']} passed. Failures by layer: {g['failures_by_layer'] or 'none'}", "",
              "| Case | Expected | Decision | Citations | Result |", "|---|---|---|---|---|"]
    lines += [f"| {c['id']} | {c['expected']} | {c['decision']} | {', '.join(c['citations']) or '-'} | {c['result']} |" for c in report["cases"]]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kfabric")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    sub.add_parser("eval")
    sub.add_parser("demo")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--roles", default="employee")
    a.add_argument("--as-of", default=None)
    a.add_argument("--action", default="inform")
    args = ap.parse_args(argv)
    if args.cmd in ("ingest", "demo"):
        _print_ingest(ingest())
    if args.cmd == "ask":
        print(json.dumps(ask(args.question, roles=args.roles.split(","), as_of=args.as_of, action=args.action), indent=2))
    if args.cmd in ("eval", "demo"):
        report = run()
        os.makedirs("reports", exist_ok=True)
        with open(os.path.join("reports", "latest.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        text = _summary(report)
        print("\n" + text)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        return 0 if report["golden_set"]["passed"] == report["golden_set"]["cases"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
