import unittest

from app.routers.assistant import _relevant_email_matches


class RelevantEmailMatchTests(unittest.TestCase):
    def test_keeps_only_the_single_relevant_email_for_assistant(self) -> None:
        matches = [
            {"email_id": "internship", "chunk_id": "i-1", "similarity": 0.64},
            {"email_id": "damage", "chunk_id": "d-1", "similarity": 0.76},
            {"email_id": "damage", "chunk_id": "d-2", "similarity": 0.71},
            {"email_id": "quotation", "chunk_id": "q-1", "similarity": 0.66},
            {"email_id": "pdf", "chunk_id": "p-1", "similarity": 0.67},
        ]

        results = _relevant_email_matches(matches)

        self.assertEqual([(item["email_id"], item["chunk_id"]) for item in results],
                         [("damage", "d-1")])

    def test_retrieval_caps_number_of_emails_after_ranking(self) -> None:
        matches = [
            {"email_id": f"email-{index}", "similarity": 0.9 - index * 0.01}
            for index in range(5)
        ]

        results = _relevant_email_matches(matches, limit=2)

        self.assertEqual([item["email_id"] for item in results], ["email-0", "email-1"])

    def test_malformed_and_missing_scores_are_not_relevant(self) -> None:
        results = _relevant_email_matches([
            {"email_id": "missing-score"},
            {"email_id": "invalid-score", "similarity": "high"},
            {"similarity": 0.9},
            None,
        ])

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
