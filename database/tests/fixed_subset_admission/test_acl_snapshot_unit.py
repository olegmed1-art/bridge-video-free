"""Offline regressions for ACL catalog row decoding and PG18 role bootstrap."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "recovery" / "fixed_subset"))
import acl_snapshot as acl


class Cursor:
    description = (SimpleNamespace(name="name"), SimpleNamespace(name="owner"))

    def __init__(self, rows):
        self.rows = rows

    def execute(self, _sql, _params):
        pass

    def fetchall(self):
        return self.rows


def test_tuple_and_dict_row_decode_to_identical_catalog_values():
    expected = [{"name": "job", "owner": "neondb_owner"}]
    assert acl._rows(Cursor([("job", "neondb_owner")]), "SELECT") == expected
    assert acl._rows(Cursor([{"name": "job", "owner": "neondb_owner"}]), "SELECT") == expected
    with pytest.raises(ValueError, match="keys differ"):
        acl._rows(Cursor([{"name": "job", "unexpected": "neondb_owner"}]), "SELECT")
    with pytest.raises(ValueError, match="width differs"):
        acl._rows(Cursor([("job",)]), "SELECT")


def role(name):
    return dict(name=name, can_login=False, superuser=False, create_role=False,
                create_db=False, inherit=False, bypass_rls=False, replication=False)


def baseline():
    return dict(
        format="fixed-subset-acl-owner-v1", schema="video_queue",
        objects=[dict(kind="schema", name="video_queue", owner="neondb_owner",
                      raw_acl=None, definition_md5=None)],
        roles=[role("neondb_owner"), role("bridge_school_worker")],
        memberships=[], columns=[], grants=[], column_grants=[],
        default_privileges=[], default_grants=[], effective_global_function_grants=[],
    )


def bootstrap():
    return dict(role="video_subset_owner", member="neondb_owner",
                grantor="neondb_owner", admin_option=True,
                inherit_option=False, set_option=False)


def test_exact_admin_only_bootstrap_is_allowed_after_recovery():
    before = baseline()
    after = deepcopy(before)
    after["roles"].append(role("video_subset_owner"))
    after["memberships"].append(bootstrap())
    acl.assert_restored(before, after)


@pytest.mark.parametrize("change", [
    {"admin_option": False}, {"inherit_option": True}, {"set_option": True},
    {"grantor": "bridge_school_worker"}, {"member": "bridge_school_worker"},
    {"role": "video_subset_unlisted"},
])
def test_non_bootstrap_memberships_remain_blocked(change):
    before = baseline()
    after = deepcopy(before)
    after["roles"].append(role("video_subset_owner"))
    edge = bootstrap()
    edge.update(change)
    after["memberships"].append(edge)
    with pytest.raises(AssertionError, match="memberships differs"):
        acl.assert_restored(before, after)


def test_existing_role_membership_must_still_match_exactly():
    before = baseline()
    after = deepcopy(before)
    after["memberships"].append(dict(role="bridge_school_worker", member="neondb_owner",
                                     grantor="neondb_owner", admin_option=True,
                                     inherit_option=False, set_option=False))
    with pytest.raises(AssertionError, match="memberships differs"):
        acl.assert_restored(before, after)
