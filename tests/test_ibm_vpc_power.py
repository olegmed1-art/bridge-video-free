from __future__ import annotations

import unittest
from pathlib import Path

from ops.ibm_vpc_power import (
    BoundedClientError,
    MUTATION_AUTHORIZATION,
    _instance_url,
    parse_instance,
)


INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
INSTANCE_NAME = "bridge-school-compute-ibm"
ROOT = Path(__file__).resolve().parents[1]


class IbmVpcPowerContractTests(unittest.TestCase):
    def test_exact_target_and_known_state_are_accepted(self) -> None:
        instance = parse_instance(
            {"id": INSTANCE_ID, "name": INSTANCE_NAME, "status": "running"},
            expected_id=INSTANCE_ID,
            expected_name=INSTANCE_NAME,
        )
        self.assertEqual("running", instance.status)

    def test_different_id_is_refused(self) -> None:
        with self.assertRaisesRegex(BoundedClientError, "instance_id_mismatch"):
            parse_instance(
                {"id": "02c7_other", "name": INSTANCE_NAME, "status": "running"},
                expected_id=INSTANCE_ID,
                expected_name=INSTANCE_NAME,
            )

    def test_different_name_is_refused(self) -> None:
        with self.assertRaisesRegex(BoundedClientError, "instance_name_mismatch"):
            parse_instance(
                {"id": INSTANCE_ID, "name": "other", "status": "running"},
                expected_id=INSTANCE_ID,
                expected_name=INSTANCE_NAME,
            )

    def test_unknown_state_is_refused(self) -> None:
        with self.assertRaisesRegex(BoundedClientError, "instance_status_unknown"):
            parse_instance(
                {"id": INSTANCE_ID, "name": INSTANCE_NAME, "status": "surprising"},
                expected_id=INSTANCE_ID,
                expected_name=INSTANCE_NAME,
            )

    def test_region_and_generation_are_pinned(self) -> None:
        url = _instance_url("eu-de", INSTANCE_ID)
        self.assertTrue(url.startswith("https://eu-de.iaas.cloud.ibm.com/"))
        self.assertIn("generation=2", url)
        with self.assertRaisesRegex(BoundedClientError, "region_not_pinned"):
            _instance_url("us-south", INSTANCE_ID)

    def test_mutation_authorization_is_exact_constant(self) -> None:
        self.assertEqual("IBM_POWER_MUTATION_AUTHORIZED=YES", MUTATION_AUTHORIZATION)

    def test_probe_workflow_is_read_only(self) -> None:
        workflow = (ROOT / ".github/workflows/ibm-vpc-power-probe.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ibm_vpc_power.py status", workflow)
        self.assertNotIn("ibm_vpc_power.py start", workflow)
        self.assertNotIn("ibm_vpc_power.py stop", workflow)
        self.assertNotIn("--mutation-authorization", workflow)


if __name__ == "__main__":
    unittest.main()
