import datetime as dt
import os
import tempfile
import unittest

from kfabric.evaluate import attribute, run
from kfabric.ingest import ingest, jaccard, minhash
from kfabric.retrieve import ask

CLAIMS = ["employee", "claims"]


class PocTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = os.path.join(tempfile.mkdtemp(), "test.db")
        results = ingest(db_path=cls.db, today=dt.date(2026, 11, 18))
        cls.outcome = {r["id"]: r["outcome"] for r in results}

    def test_ingestion_outcomes(self):
        expected = {"RC-231": "accept", "RC-214": "reference_only", "PRD-INTAKE-V3-COPY": "reference_only",
                    "PRD-INTAKE-V3-EXPORT": "reject", "POL-REGIONAL-MEMO": "human_review", "POL-ROADSIDE-DRAFT": "quarantine",
                    "REPO-LEGACY-EXPORT": "quarantine", "SCAN-OLD-MEMO": "reference_only", "FORM-CLAIMS-PDF": "reject"}
        for asset, outcome in expected.items():
            self.assertEqual(self.outcome[asset], outcome, asset)

    def test_unchanged_content_is_skipped(self):
        again = ingest(db_path=self.db, today=dt.date(2026, 11, 18), fresh=False)
        self.assertTrue(all(r["outcome"] == "unchanged" for r in again))

    def test_current_version_wins(self):
        res = ask("What is the towing limit?", roles=CLAIMS, as_of="2026-11-18", db_path=self.db)
        self.assertIn("POL-ROADSIDE-V2", res["citations"])
        self.assertNotIn("POL-ROADSIDE-V1", res["citations"])
        self.assertTrue(any("100 miles" in a for a in res["answer"]))

    def test_answer_as_of_an_earlier_date(self):
        res = ask("What is the towing limit?", roles=CLAIMS, as_of="2024-06-01", db_path=self.db)
        self.assertEqual(res["citations"], ["POL-ROADSIDE-V1"])
        self.assertTrue(any("50 miles" in a for a in res["answer"]))

    def test_access_is_filtered_before_search(self):
        self.assertEqual(ask("What is the staff incentive rate?", roles=CLAIMS, db_path=self.db)["decision"], "abstain")
        self.assertEqual(ask("What is the staff incentive rate?", roles=["hr"], db_path=self.db)["citations"], ["POL-STAFF-INCENTIVE"])

    def test_abstains_and_flags_gaps(self):
        self.assertEqual(ask("What is the drone delivery policy?", roles=CLAIMS, db_path=self.db)["decision"], "abstain")
        gap = ask("What is the payment rule for roadside claims?", roles=CLAIMS, db_path=self.db)
        self.assertEqual(gap["decision"], "answer_with_gap")
        self.assertIn("payment", gap["missing"])

    def test_higher_risk_actions_need_higher_trust(self):
        inform = ask("What was decided about intake photos in the claims sync?", roles=CLAIMS, db_path=self.db)
        act = ask("What was decided about intake photos in the claims sync?", roles=CLAIMS, action="act", db_path=self.db)
        self.assertIn("MEET-2026-10-02", inform["citations"])
        self.assertNotIn("MEET-2026-10-02", act["citations"])

    def test_near_duplicate_detection(self):
        a = "members raise a roadside claim from the mobile app and upload two photos of the vehicle before submission"
        self.assertGreater(jaccard(minhash(a), minhash(a + " today")), 0.6)
        self.assertLess(jaccard(minhash(a), minhash("the staff incentive is reviewed once a year by the committee")), 0.2)

    def test_failure_attribution(self):
        item = {"expect": "answer", "expect_sources": ["A"]}
        base = {"decision": "answer", "citations": [], "retrieved": []}
        self.assertEqual(attribute(item, base, accepted=set()), "source")
        self.assertEqual(attribute(item, base, accepted={"A"}), "retrieval")
        self.assertEqual(attribute(item, dict(base, retrieved=["A"]), accepted={"A"}), "generation")
        self.assertIsNone(attribute(item, dict(base, retrieved=["A"], citations=["A"]), accepted={"A"}))
        self.assertEqual(attribute({"expect": "abstain"}, base, accepted=set()), "policy")

    def test_golden_set_passes(self):
        report = run(db_path=self.db)
        self.assertEqual(report["golden_set"]["passed"], report["golden_set"]["cases"], report["golden_set"])


if __name__ == "__main__":
    unittest.main()
