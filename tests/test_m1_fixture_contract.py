import json
from pathlib import Path

from assistant_lab.research_pipeline import plan_execution


def test_m1_fixture_is_executable_dds3_research_job():
    fixture = json.loads(Path("assistant_lab/research_m1_fixture.json").read_text(encoding="utf-8"))
    plan = plan_execution(fixture["kind"], fixture["payload"])
    assert plan.assistant_lab_kind == "DDS3_COMPUTE"
