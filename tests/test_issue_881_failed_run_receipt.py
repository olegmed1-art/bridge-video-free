from ops.issue_881_failed_run_receipt import RESOURCE_FIELDS, parse_receipt


RUN_ID = "33919714953"
PREFIX = "issue-881-root-recovery-fdb6c3766909fe4e-run-"


def receipt(*, instance="none", create_status="REQUEST_UNCERTAIN", create_rc="1"):
    ids = {
        "instance": instance,
        "boot-volume": "ocid1.bootvolume.oc1.region.volume",
        "vcn": "ocid1.vcn.oc1.region.vcn",
        "internet-gateway": "ocid1.internetgateway.oc1.region.ig",
        "route-table": "ocid1.routetable.oc1.region.route",
        "security-list": "ocid1.securitylist.oc1.region.security",
        "subnet": "ocid1.subnet.oc1.region.subnet",
    }
    lines = [
        "- operation: `EXPAND_AND_RECOVER`",
        "- workflow outcome: `failure`",
        f"- current-run stamp: `{PREFIX}{RUN_ID}-a1`",
        f"- isolated instance create request: `{create_status}`; rc: `{create_rc}`",
    ]
    lines.extend(f"- {RESOURCE_FIELDS[kind]}: `{value}`" for kind, value in ids.items())
    lines.append(f"- run: https://github.com/olegmed1-art/bridge-video-free/actions/runs/{RUN_ID}")
    return "\n".join(lines) + "\n"


def test_parses_authoritative_typed_ids_and_binds_stamp_and_hash():
    parsed = parse_receipt(receipt(), RUN_ID, PREFIX)
    assert parsed["stamp"] == f"{PREFIX}{RUN_ID}-a1"
    assert parsed["resources"]["instance"] == []
    assert parsed["instance_create_rc"] == 1
    assert parsed["resources"]["boot-volume"] == ["ocid1.bootvolume.oc1.region.volume"]
    assert len(parsed["receipt_sha256"]) == 64


def _assert_rejected(body):
    try:
        parse_receipt(body, RUN_ID, PREFIX)
    except ValueError:
        return
    raise AssertionError("malformed receipt was accepted")


def test_rejects_malformed_or_mismatched_receipt():
    for old, new in [
        ("workflow outcome: `failure`", "workflow outcome: `success`"),
        (RUN_ID, "33919714954"),
        ("temporary VCN ID: `ocid1.vcn", "temporary VCN ID: `none`\n- ignored: `ocid1.vcn"),
        ("ocid1.subnet", "ocid1.vcn"),
    ]:
        _assert_rejected(receipt().replace(old, new, 1))


def test_rejects_ocid_prefix_tricks_and_publicly_unsafe_tokens():
    for invalid in (
        "ocid1.vcn.evil.oc1.region.value",
        "ocid1.vcn.oc1.region.value/extra",
        "ocid1.vcn.oc1.region.value?query",
        "ocid1.VCN.oc1.region.value",
    ):
        _assert_rejected(receipt().replace("ocid1.vcn.oc1.region.vcn", invalid))


def test_requires_uncertain_status_when_instance_id_is_missing():
    _assert_rejected(receipt(create_status="CAPTURED"))


def test_requires_complete_numeric_create_request_rc():
    _assert_rejected(receipt().replace("; rc: `1`", ""))
    _assert_rejected(receipt(create_rc="unknown"))
    _assert_rejected(receipt().replace("; rc: `1`", "; rc: `1`; trailing: `x`"))


def test_accepts_authoritative_instance_id_when_launch_was_captured():
    parsed = parse_receipt(
        receipt(instance="ocid1.instance.oc1.region.instance", create_status="CAPTURED"),
        RUN_ID,
        PREFIX,
    )
    assert parsed["resources"]["instance"] == ["ocid1.instance.oc1.region.instance"]
