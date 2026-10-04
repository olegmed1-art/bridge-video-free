from copy import deepcopy
import unittest

from bridge_contracts.book_material import InvalidBookMaterial, digest
from bridge_contracts.book_world import CHECKS, WORLD_SCHEMA, build_world_publication, publication_version, render_teacher_book
from test_book_material_staging import bundle, record


def reviewed_fixture():
    value = bundle()
    obj = value["objects"][0]
    receipt = {"schema": "book-source-review-v1", "review_id": "SYNTHETIC-REVIEW",
               "reviewer": "synthetic-test-reviewer", "independence_group": "synthetic-formal-check",
               "assurance": "I2", "rendition_sha256": value["source"]["rendition_sha256"],
               "source_size_bytes": 42, "source_page_count": 3,
               "claims": [{"object_id": obj["object_id"], "content_sha256": digest(obj["content"]),
                           "anchor_sha256": digest(obj["anchor"]), "verification": "M1",
                           "checks": {k: "PASS" for k in CHECKS}, "positive_example_ref": "synthetic-positive",
                           "counterexample_ref": "synthetic-negative", "review_evidence_ref": "synthetic-review"}]}
    return value, receipt


def stored_fixture():
    value, receipt = reviewed_fixture()
    publication = build_world_publication(value, receipt)
    record = publication["records"][0]
    item = {"stable_key": record["stable_key"], "item_id": "synthetic-version",
            "content": record["content"], "authority_class": "external", "review_status": "reviewed",
            "version_status": "active", "system_profile": "SYSTEM_NEUTRAL",
            "provenance": {"schema": WORLD_SCHEMA, "review_receipt": receipt,
                           "version_hash": publication_version(record, receipt),
                           "source_id": "22222222-2222-2222-2222-222222222222"},
            "sources": [{"source_id": "22222222-2222-2222-2222-222222222222",
                         "source_locator": record["content"]["citation"],
                         "status": "active",
                         "canonical_locator": value["source"]["locator"]}]}
    return item


class BookWorldTests(unittest.TestCase):
    def test_structural_schema_with_independent_jsonschema_engine(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema engine is installed in branch CI")
        import json
        from pathlib import Path
        schema = json.loads(Path("schemas/book-material-staging-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        value, _ = reviewed_fixture()
        validator.validate(value)
        value["objects"][0]["anchor"]["pdf_page_index"] = True
        self.assertTrue(list(validator.iter_errors(value)))

    def test_bound_source_proof_required(self):
        value, receipt = reviewed_fixture()
        receipt["rendition_sha256"] = "0" * 64
        with self.assertRaisesRegex(InvalidBookMaterial, "SOURCE_PROOF_BINDING"):
            build_world_publication(value, receipt)

    def test_no_silent_upgrade_of_source_only_review(self):
        value, receipt = reviewed_fixture()
        receipt["assurance"] = "I0"
        with self.assertRaisesRegex(InvalidBookMaterial, "INDEPENDENT_REVIEW_REQUIRED"):
            build_world_publication(value, receipt)
        receipt["assurance"] = "I2"
        receipt["claims"][0]["checks"]["semantic_dedup"] = "PENDING"
        with self.assertRaisesRegex(InvalidBookMaterial, "REVIEW_CHECK_FAILED"):
            build_world_publication(value, receipt)

    def test_changed_claim_or_anchor_invalidates_review(self):
        value, receipt = reviewed_fixture()
        receipt["claims"][0]["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(InvalidBookMaterial, "CLAIM_PROOF_BINDING"):
            build_world_publication(value, receipt)

    def test_unresolved_errata_blocks_publication(self):
        value, receipt = reviewed_fixture()
        value["objects"][0]["errata_ids"] = ["SYNTHETIC-ERRATUM"]
        with self.assertRaisesRegex(InvalidBookMaterial, "ATOM_NOT_PUBLISHABLE"):
            build_world_publication(value, receipt)

    def test_rerun_does_not_reidentify_reviewed_knowledge(self):
        value, receipt = reviewed_fixture()
        first = build_world_publication(value, receipt)
        value["run"].update(run_id="new-run", model_version="new-model")
        second = build_world_publication(value, receipt)
        self.assertEqual(first["publication_hash"], second["publication_hash"])
        self.assertEqual(first["records"], second["records"])

    def test_bounded_atom_budget(self):
        value, receipt = reviewed_fixture()
        receipt["claims"] *= 4
        with self.assertRaisesRegex(InvalidBookMaterial, "ATOM_BUDGET"):
            build_world_publication(value, receipt)

    def test_teacher_preserves_version_and_citation_without_canon_claim(self):
        item = stored_fixture()
        result = render_teacher_book(item)
        self.assertEqual(result["knowledge_version_id"], "synthetic-version")
        self.assertEqual(result["authority_lane"], "WORLD_EXTERNAL")
        self.assertEqual(result["citations"][0]["printed_page_label"], "iv")
        self.assertEqual(result["pdf_page_number_1based"], 1)
        self.assertFalse(result["canon_activation_performed"])

    def test_teacher_rejects_preview_archived_tampered_or_unlinked_content(self):
        for field, value in [("version_status", "archived"), ("authority_class", "school_canon"),
                             ("review_status", "unreviewed"), ("sources", [])]:
            item = stored_fixture()
            item[field] = value
            with self.subTest(field=field), self.assertRaises(InvalidBookMaterial):
                render_teacher_book(item)
        item = stored_fixture()
        item["content"]["statement"] = "Tampered."
        with self.assertRaisesRegex(InvalidBookMaterial, "BOOK_WORLD_VERSION_HASH"):
            render_teacher_book(item)


if __name__ == "__main__":
    unittest.main()



def reviewed_identity_fixture(value, receipt, school_id, source_id, stored_locator):
    """Synthetic operator review; never a real source/registry attestation."""
    from bridge_contracts.book_world import world_uuid
    source = value["source"]
    return {"schema": "book-source-identity-review-v1", "review_id": "SYNTHETIC-IDENTITY",
            "reviewer": "synthetic-identity-reviewer", "independence_group": "synthetic-identity-group",
            "assurance": "I2", "source_descriptor_sha256": digest(source),
            "school_id": school_id, "registry_source_id": source_id,
            "stored_locator": stored_locator, "citation_locator": source["locator"],
            "rendition_sha256": source["rendition_sha256"],
            "source_size_bytes": receipt["source_size_bytes"], "source_page_count": source["page_count"],
            "asset_id": world_uuid("asset", school_id, source["rendition_sha256"]), "asset_action": "CREATE"}
