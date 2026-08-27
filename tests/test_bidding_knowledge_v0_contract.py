from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "migrations" / "0105_bidding_knowledge_v0.sql"
REGISTRY_MARKER = (
    "-- checksum is the SHA-256 of this file up to "
    "(but not including) this registry block."
)


def table_block(sql: str, qualified_name: str) -> str:
    marker = f"CREATE TABLE {qualified_name}"
    start = sql.index(marker)
    open_paren = sql.index("(", start)
    depth = 0
    in_single_quote = False
    i = open_paren
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_single_quote and i + 1 < len(sql) and sql[i + 1] == "'":
                i += 2
                continue
            in_single_quote = not in_single_quote
        elif not in_single_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return sql[start : i + 1]
        i += 1
    raise AssertionError(f"unterminated CREATE TABLE for {qualified_name}")


class BiddingKnowledgeV0ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_checksum_is_reproducible(self) -> None:
        body, registry = self.sql.split(REGISTRY_MARKER, 1)
        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        match = re.search(
            r"VALUES \('0105_bidding_knowledge_v0', '([0-9a-f]{64})'\)",
            registry,
        )
        self.assertIsNotNone(match)
        self.assertEqual(expected, match.group(1))

    def test_parser_compatible_function_bodies(self) -> None:
        self.assertNotIn("$$", self.sql)
        self.assertNotIn("$function$", self.sql.lower())
        self.assertIn("LANGUAGE plpgsql\nAS '", self.sql)

    def test_required_objects_exist(self) -> None:
        for name in (
            "bidding.rule",
            "bidding.rule_relation",
            "bidding.rule_test",
            "bidding.rule_conflict",
            "bidding.runtime_activation",
            "bidding.decision_trace",
        ):
            self.assertIn(f"CREATE TABLE {name}", self.sql)
        for name in (
            "bidding.active_school_canon_rule_v",
            "bidding.active_world_rule_v",
            "bidding.canon_world_link_v",
        ):
            self.assertIn(f"CREATE OR REPLACE VIEW {name}", self.sql)

    def test_no_hidden_hand_columns_exist(self) -> None:
        forbidden = (
            "partner_hand",
            "opponent_hand",
            "opponent_hands",
            "north_hand",
            "east_hand",
            "south_hand",
            "west_hand",
            "full_deal",
            "hidden_cards",
        )
        for name in (
            "bidding.rule",
            "bidding.runtime_activation",
            "bidding.decision_trace",
        ):
            block = table_block(self.sql, name)
            for column in forbidden:
                self.assertIsNone(
                    re.search(rf"(?im)^\s*{re.escape(column)}\s+", block),
                    f"forbidden runtime column {column} in {name}",
                )
        trace = table_block(self.sql, "bidding.decision_trace")
        self.assertRegex(trace, r"(?m)^\s*acting_hand\s+jsonb\s+NOT NULL")
        self.assertIn(
            "CHECK (NOT bidding.contains_forbidden_hidden_key(public_context))",
            trace,
        )

    def test_activation_is_fail_closed(self) -> None:
        for message in (
            "BID_ACTIVATION_RULE_NOT_VALIDATED",
            "BID_ACTIVATION_SOURCE_REQUIRED",
            "BID_ACTIVATION_REQUIRED_TEST_COVERAGE_MISSING",
            "BID_ACTIVATION_FAILED_TEST_PRESENT",
            "BID_ACTIVATION_OPEN_CONFLICT",
            "BID_ACTIVATION_CANON_APPROVAL_REQUIRED",
            "BID_ACTIVATION_AUTHORITY_NOT_RUNTIME_ELIGIBLE",
        ):
            self.assertIn(message, self.sql)
        for test_type in (
            "positive",
            "negative",
            "boundary",
            "hidden_information",
        ):
            self.assertIn(f"''{test_type}''", self.sql)

    def test_authority_lanes_are_structurally_separate(self) -> None:
        canon_view = self.sql.split(
            "CREATE OR REPLACE VIEW bidding.active_school_canon_rule_v AS", 1
        )[1].split("CREATE OR REPLACE VIEW bidding.active_world_rule_v AS", 1)[0]
        world_view = self.sql.split(
            "CREATE OR REPLACE VIEW bidding.active_world_rule_v AS", 1
        )[1].split("CREATE OR REPLACE VIEW bidding.canon_world_link_v AS", 1)[0]
        self.assertIn("kv.authority_class = 'school_canon'", canon_view)
        self.assertIn("ra.authority_lane = 'school_canon'", canon_view)
        self.assertIn("JOIN public.canon_activation", canon_view)
        self.assertNotIn("kv.authority_class = 'external'", canon_view)
        self.assertIn("kv.authority_class = 'external'", world_view)
        self.assertIn("ra.authority_lane = 'world_external'", world_view)
        self.assertIn("ra.canon_activation_id IS NULL", world_view)
        self.assertNotIn("JOIN public.canon_activation", world_view)

    def test_world_catalog_is_explicitly_opt_in(self) -> None:
        function = self.sql.split(
            "CREATE OR REPLACE FUNCTION bidding.get_runtime_rule_catalog", 1
        )[1].split("COMMENT ON FUNCTION bidding.get_runtime_rule_catalog", 1)[0]
        self.assertIn("p_include_world boolean DEFAULT false", function)
        self.assertIn("WHERE p_include_world", function)
        self.assertIn("''school_canon''::text", function)
        self.assertIn("''world_external''::text", function)

    def test_decision_trace_is_append_only(self) -> None:
        self.assertIn("CREATE TRIGGER decision_trace_append_only", self.sql)
        self.assertIn("BEFORE UPDATE OR DELETE ON bidding.decision_trace", self.sql)
        self.assertIn("BID_DECISION_TRACE_APPEND_ONLY", self.sql)
        self.assertIn(
            "REVOKE UPDATE, DELETE, TRUNCATE\n  ON bidding.decision_trace",
            self.sql,
        )

    def test_worker_cannot_mutate_active_runtime_rules(self) -> None:
        self.assertIn("CREATE TRIGGER worker_active_rule_immutable", self.sql)
        self.assertIn("BEFORE UPDATE OR DELETE ON bidding.rule", self.sql)
        self.assertIn("current_user = ''bridge_school_worker''", self.sql)
        self.assertIn("BID_ACTIVE_RULE_WORKER_IMMUTABLE", self.sql)

    def test_worker_cannot_resolve_rule_conflicts(self) -> None:
        self.assertRegex(
            self.sql,
            r"GRANT\s+INSERT\s+ON\s+bidding\.rule_conflict\s+TO\s+bridge_school_worker",
        )
        self.assertNotRegex(
            self.sql,
            r"GRANT\s+INSERT,\s+UPDATE[^;]+bidding\.rule_conflict[^;]+bridge_school_worker",
        )
        self.assertIn(
            "REVOKE UPDATE, DELETE, TRUNCATE\n  ON bidding.rule_conflict",
            self.sql,
        )

    def test_worker_cannot_activate_rules(self) -> None:
        self.assertIn(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE\n  ON bidding.runtime_activation",
            self.sql,
        )
        self.assertNotRegex(
            self.sql,
            r"GRANT\s+INSERT[^;]+ON\s+bidding\.runtime_activation[^;]+bridge_school_worker",
        )

    def test_migration_contains_no_bridge_meaning(self) -> None:
        # Infrastructure migration must not seed a system, convention, range or call.
        self.assertNotIn("INSERT INTO bidding.rule", self.sql)
        self.assertNotIn("INSERT INTO public.knowledge_version", self.sql)
        self.assertNotRegex(self.sql, r"(?i)VALUES\s*\([^;]*(?:'1[CDHSN]'|'2[CDHSN]')")


if __name__ == "__main__":
    unittest.main()
