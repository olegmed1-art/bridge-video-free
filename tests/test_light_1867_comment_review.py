import copy
import hashlib
import pytest
from ops import light_1867_comment_review as gate


@pytest.fixture
def comments(monkeypatch):
    rows = [{'id': 1, 'body': 'Diagnostic ' + gate.DISPATCH}, {'id': 2, 'body': 'Unrelated'}]
    monkeypatch.setattr(gate, 'DIAGNOSTICS', {1: hashlib.sha256(rows[0]['body'].encode()).hexdigest()})
    return rows


@pytest.mark.parametrize('change,reason', [
    (lambda c: c[0].update(body=c[0]['body'] + ' edited'), 'DIAGNOSTIC_CHANGED'),
    (lambda c: c.pop(0), 'DIAGNOSTIC_MISSING'),
    (lambda c: c.append(dict(c[0])), 'COMMENT_ID_OR_BODY_INVALID'),
    (lambda c: c.append({'id': 3, 'body': gate.DISPATCH.upper()}), 'UNREVIEWED_DISPATCH_MENTION'),
    (lambda c: c.append({'id': 3, 'body': '  @codex ' + gate.DISPATCH}), 'COMMAND_EVIDENCE_PRESENT'),
    (lambda c: c.append({'id': 3, 'body': 'SLAVIK_CODEX_DISPATCH_V1 ' + gate.DISPATCH}), 'COMMAND_EVIDENCE_PRESENT'),
    (lambda c: c.append({'id': True, 'body': ''}), 'COMMENT_ID_OR_BODY_INVALID'),
    (lambda c: c.append({'id': 3, 'body': None}), 'COMMENT_ID_OR_BODY_INVALID'),
])
def test_rejects_conflicting_evidence(comments, change, reason):
    change(comments)
    with pytest.raises(ValueError, match=reason):
        gate.inspect(comments)


def test_order_independent_inventory(comments):
    assert gate.inspect(comments) == gate.inspect(list(reversed(comments)))


def fake_api(monkeypatch, comments, drift=False, wrong_head=False):
    calls = []
    def read(endpoint, paginate=False):
        calls.append((endpoint, paginate))
        if paginate:
            rows = copy.deepcopy(comments)
            if drift and len(calls) == 3:
                rows.append({'id': 3, 'body': 'New unrelated comment'})
            return [[rows[0]], rows[1:]]
        return {'number': gate.TARGET, 'state': 'open', 'head': {'sha': '0'*40 if wrong_head else gate.HEAD}}
    monkeypatch.setattr(gate, 'gh_json', read)
    return calls


def test_matching_complete_scans_never_authorize_send(monkeypatch, comments):
    calls = fake_api(monkeypatch, comments)
    result = gate.collect()
    assert result['send_authorized'] is False
    assert result['live_bridge_gate'] == 'BLOCKED'
    assert len(calls) == 4
    assert [p for _, p in calls] == [False, True, True, False]


def test_concurrent_comment_change_rejected(monkeypatch, comments):
    fake_api(monkeypatch, comments, drift=True)
    with pytest.raises(ValueError, match='COMMENT_SCAN_DRIFT'):
        gate.collect()


def test_wrong_head_rejected_before_comments(monkeypatch, comments):
    calls = fake_api(monkeypatch, comments, wrong_head=True)
    with pytest.raises(ValueError, match='TARGET_DRIFT'):
        gate.collect()
    assert len(calls) == 1


def test_transport_failure_not_treated_as_empty_page(monkeypatch):
    def failed(*args, **kwargs):
        raise OSError('offline')
    monkeypatch.setattr(gate, 'gh_json', failed)
    with pytest.raises(OSError):
        gate.collect()
