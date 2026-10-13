"""Lexical SQL namespace guards only; these do not compile or execute PostgreSQL."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATHS = (
    "database/migrations/0060_fixed_subset_admission.sql",
    "database/rollback/0060_fixed_subset_admission.sql",
    "database/tests/fixed_subset_admission/helpers.sql",
)
FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION (?P<name>[\w.]+)"
    r"\((?P<inputs>.*?)\)\s+RETURNS (?P<returns>.*?)\bLANGUAGE\b"
    r"(?P<options>.*?)\bAS \$\$(?P<body>.*?)\$\$;", re.S
)
DO = re.compile(r"DO (?P<tag>\$\w+\$)(?P<body>.*?)END (?P=tag);", re.S)
QUOTED = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", re.S)


def code_only(source):
    # Preserve positions, so a dot before a member reference remains observable.
    return QUOTED.sub(lambda match: " " * len(match.group()), source)


def namespace_findings(body, inputs=""):
    code = code_only(body)
    declaration = re.search(r"\bDECLARE\b(.*?)\bBEGIN\b", code, re.S)
    local_names = []
    if declaration:
        local_names = [
            match.group(1)
            for item in declaration.group(1).split(";")
            if (match := re.match(r"\s*(\w+)\s+\w", item))
        ]
    input_names = [
        item.strip().split()[0] for item in inputs.split(",") if item.strip()
    ]
    aliases = re.findall(r"\bAS\s+(q_\w+)\b", code)
    findings = []
    if any(not name.startswith("v_") for name in local_names):
        findings.append("local namespace")
    if any(not name.startswith("p_") for name in input_names):
        findings.append("input namespace")
    if set(local_names) & (set(input_names) | set(aliases)):
        findings.append("overlapping namespaces")
    for prefix, declared in (("v_", local_names), ("p_", input_names), ("q_", aliases)):
        referenced = {
            match.group() for match in re.finditer(r"\b" + prefix + r"\w+\b", code)
            if not code[:match.start()].rstrip().endswith(".")
        }
        if not referenced <= set(declared):
            findings.append("undeclared " + prefix)
    # Named table sources, not function calls or INSERT/SET destination columns.
    for match in re.finditer(
        r"\b(?:FROM|JOIN|UPDATE)\s+(?:video_queue|admission_test)\.\w+\b"
        r"(?!\s*\()(?:\s+AS\s+(?P<alias>\w+))?", code
    ):
        if not (match.group("alias") or "").startswith("q_"):
            findings.append("table alias namespace")
    if re.search(r"(?:#|SET\s+)variable_conflict\b|\bplpgsql\.variable_conflict\b", code):
        findings.append("conflict setting")
    return findings


@pytest.mark.parametrize("path", SQL_PATHS)
def test_all_routines_have_disjoint_declared_namespaces(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    functions = list(FUNCTION.finditer(source))
    blocks = list(DO.finditer(source))
    assert functions or blocks
    for function in functions:
        assert not namespace_findings(function["body"], function["inputs"]), function["name"]
    for block in blocks:
        # Rollback has no local variables, column expressions or aliases.
        if "rollback" not in block["tag"]:
            assert not namespace_findings(block["body"]), block["tag"]
    assert "plpgsql.variable_conflict" not in source
    assert "#variable_conflict" not in source


def test_namespace_guard_detects_the_observed_collision_class():
    bad = """DECLARE a video_queue.subset_approval%ROWTYPE;
    BEGIN SELECT a.contract INTO a FROM video_queue.subset_approval AS a; END"""
    assert "local namespace" in namespace_findings(bad)
    assert "table alias namespace" in namespace_findings(bad)


def test_namespace_guard_detects_a_missed_rename_and_unsafe_alias():
    bad = """DECLARE v_approval video_queue.subset_approval%ROWTYPE;
    BEGIN SELECT q_approval.contract INTO v_missing
    FROM video_queue.subset_approval AS v_approval; END"""
    findings = namespace_findings(bad)
    assert "undeclared v_" in findings
    assert "undeclared q_" in findings
    assert "table alias namespace" in findings


def routines():
    source = (ROOT / SQL_PATHS[0]).read_text(encoding="utf-8")
    return {m["name"].split(".")[-1]: m for m in FUNCTION.finditer(source)}


@pytest.mark.parametrize("wrapper,underlying,alias,columns", [
    ("retry_subset_job", "retry_job", "q_retry",
     "job_status text,batch_status text,retry_after timestamptz"),
    ("finish_subset_job", "finish_job", "q_finish",
     "job_status text,batch_status text,released_jobs integer"),
])
def test_wrapper_return_contracts_keep_public_names_types_and_explicit_order(
    wrapper, underlying, alias, columns
):
    function = routines()[wrapper]
    assert re.sub(r"\s+", "", function["returns"]) == "TABLE(" + columns.replace(" ", "") + ")"
    names = [item.split()[0] for item in columns.split(",")]
    expected = ",".join(alias + "." + name for name in names)
    assert f"RETURN QUERY SELECT {expected} FROM video_queue.{underlying}(" in function["body"]
    assert f"AS {alias};" in function["body"]


def test_claim_return_contract_and_record_returning_remain_qualified():
    claim = routines()["claim_job"]
    names = re.findall(r"(\w+)\s+(?:uuid|integer|text|bigint|boolean)", claim["returns"])
    assert names == [
        "job_id", "batch_id", "lease_token", "sequence", "source_folder_id",
        "output_folder_id", "work_folder_id", "processing_profile", "algorithm_revision",
        "source_file_id", "source_name", "source_mime_type", "source_size_bytes",
        "source_checksum", "stable_job_key", "is_canary", "attempt_count",
    ]
    projection = claim["body"].split("RETURN QUERY", 1)[1].split("FROM", 1)[0]
    for name in names:
        assert re.search(r"\b(?:q_batch|v_job)\." + name + r"\b", projection)
    assert "RETURNING q_job.* INTO v_job;" in claim["body"]
    assert "RETURNING *" not in claim["body"]


def test_case_expressions_in_if_conditions_keep_inner_then_parenthesized():
    # Narrow guard for the two observed parser defects; not general SQL parsing.
    functions = routines()
    assert "<> (CASE WHEN p_only_file IS NULL THEN" in functions["subset_observed_sources"]["body"]
    assert "IS DISTINCT FROM (CASE WHEN NEW.is_canary THEN" in functions["subset_guard"]["body"]
