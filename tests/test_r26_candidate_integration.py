"""Review-wrapper integration only; fake I/O ports, no video accuracy claim."""
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys

import pytest

from bridge_vision import bridgit_primary_production_candidate as adapter


TOKEN = lambda: "synthetic-unused"


def make_base(name="base"):
    inherited = [{"deal_id": "inherited"}]
    calls = []
    shots = [{"source": "inherited"}]
    base = SimpleNamespace(
        name=name, inherited=inherited, calls=calls, shots=shots,
        visual=lambda *args: (calls.append("visual") or "p1", "p2", shots),
        derive_deals_decisions=lambda *args: (inherited, []),
        master_analysis_payload=lambda *args, **kwargs: {},
    )
    return base


def fake_primary(base, token, video, work, job):
    base.calls.append("primary")
    return ([{"deal_id": base.name + ":" + job}],
            [{"source": "primary", "job": job}], {"status": "PRIMARY_COMPLETE"})


def run_visual(base, tmp_path, job="one"):
    return base.visual(tmp_path / "video", tmp_path, 20, [], job)


def test_adapter_is_installed_once_and_derivation_is_repeatable(monkeypatch, tmp_path):
    base = make_base()
    monkeypatch.setattr(adapter, "_run_primary", fake_primary)
    adapter.install(base, TOKEN)
    hooks = base.visual, base.derive_deals_decisions, base.master_analysis_payload
    adapter.install(base, TOKEN)
    assert hooks == (base.visual, base.derive_deals_decisions, base.master_analysis_payload)
    _, _, shots = run_visual(base, tmp_path)
    assert base.calls == ["visual", "primary"]
    assert len(shots) == 2 and len(base.shots) == 1
    for _ in range(2):
        deals, _ = base.derive_deals_decisions([], "one")
        assert [d["deal_id"] for d in deals] == ["inherited", "base:one"]
    assert base.inherited == [{"deal_id": "inherited"}]
    assert base.master_analysis_payload(job_id="one")["technical_qc"]["card_recognizer"]["job_id"] == "one"


def test_foreign_job_and_failed_next_job_never_reuse_prior_cards(monkeypatch, tmp_path):
    base = make_base()
    monkeypatch.setattr(adapter, "_run_primary", fake_primary)
    adapter.install(base, TOKEN)
    run_visual(base, tmp_path, "one")
    assert base.derive_deals_decisions([], "other")[0] == base.inherited
    def broken(*args):
        raise RuntimeError("synthetic decoder bug")
    monkeypatch.setattr(adapter, "_run_primary", broken)
    with pytest.raises(RuntimeError, match="decoder bug"):
        run_visual(base, tmp_path, "two")
    for job in ("one", "two"):
        assert base.derive_deals_decisions([], job)[0] == base.inherited
    master = base.master_analysis_payload(job_id="two")
    assert master["content_quality"]["primary_visual_deals"] == 0
    assert master["technical_qc"]["card_recognizer"] == {"status": "FAILED", "job_id": "two"}


def test_separate_base_installations_do_not_share_results(monkeypatch, tmp_path):
    first, second = make_base("first"), make_base("second")
    monkeypatch.setattr(adapter, "_run_primary", fake_primary)
    for base in (first, second):
        adapter.install(base, TOKEN)
        run_visual(base, tmp_path)
    assert first.derive_deals_decisions([], "one")[0][-1]["deal_id"] == "first:one"
    assert second.derive_deals_decisions([], "one")[0][-1]["deal_id"] == "second:one"


@pytest.mark.parametrize("module_name", adapter.HISTORICAL_ADAPTERS)
def test_existing_primary_installation_is_rejected_before_wrapping(monkeypatch, module_name):
    base = make_base()
    original = base.visual
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(_INSTALLED_BASE_IDS={id(base)}))
    with pytest.raises(adapter.CandidateInstallationError, match="another primary"):
        adapter.install(base, TOKEN)
    assert base.visual is original
    assert not hasattr(base, adapter.STATE_ATTRIBUTE)


@pytest.mark.parametrize("replace", ["visual", "token"])
def test_changed_hooks_or_token_provider_do_not_silently_reinstall(replace):
    base = make_base()
    adapter.install(base, TOKEN)
    token = TOKEN
    if replace == "visual":
        base.visual = lambda *args: None
    else:
        token = lambda: "different"
    with pytest.raises(adapter.CandidateInstallationError, match="replaced"):
        adapter.install(base, token)


def test_reentrant_visual_job_is_rejected_and_original_failure_escapes(monkeypatch, tmp_path):
    base = make_base()
    def recursive(*args):
        run_visual(base, tmp_path, "nested")
    monkeypatch.setattr(adapter, "_run_primary", recursive)
    adapter.install(base, TOKEN)
    with pytest.raises(adapter.CandidateInstallationError, match="concurrent"):
        run_visual(base, tmp_path)
    assert base.master_analysis_payload(job_id="one")["technical_qc"]["card_recognizer"]["status"] == "FAILED"


@pytest.mark.parametrize("query", ["other", None])
def test_master_payload_never_borrows_foreign_or_unspecified_job_qc(monkeypatch, tmp_path, query):
    base = make_base()
    monkeypatch.setattr(adapter, "_run_primary", fake_primary)
    adapter.install(base, TOKEN)
    run_visual(base, tmp_path, "one")
    master = base.master_analysis_payload(**({"job_id": query} if query else {}))
    assert master["technical_qc"]["card_recognizer"] == {"status": "NOT_RUN", "job_id": query}
    assert master["content_quality"]["primary_visual_deals"] == 0
    own = base.master_analysis_payload(job_id="one")
    assert own["technical_qc"]["card_recognizer"]["status"] == "PRIMARY_COMPLETE"
    assert own["content_quality"]["primary_visual_deals"] == 1


def test_master_payload_before_visual_reports_not_run():
    base = make_base()
    adapter.install(base, TOKEN)
    master = base.master_analysis_payload(job_id="one")
    assert master["technical_qc"]["card_recognizer"] == {"status": "NOT_RUN", "job_id": "one"}
    assert master["content_quality"]["primary_visual_deals"] == 0


def test_master_payload_can_use_original_payload_job_id(monkeypatch, tmp_path):
    base = make_base()
    base.master_analysis_payload = lambda job: {"job_id": job}
    monkeypatch.setattr(adapter, "_run_primary", fake_primary)
    adapter.install(base, TOKEN)
    run_visual(base, tmp_path, "one")
    assert base.master_analysis_payload("one")["content_quality"]["primary_visual_deals"] == 1
    assert base.master_analysis_payload("other")["content_quality"]["primary_visual_deals"] == 0


def test_overlapping_candidates_on_separate_bases_are_rejected_before_io(monkeypatch, tmp_path):
    first, second = make_base("first"), make_base("second")
    for base in (first, second):
        adapter.install(base, TOKEN)
    def overlapping(base, token, video, work, job):
        if base is first:
            with pytest.raises(adapter.CandidateInstallationError, match="separate processes"):
                run_visual(second, tmp_path, "two")
            assert second.calls == []
            assert second.master_analysis_payload(job_id="two")["technical_qc"]["card_recognizer"]["status"] == "NOT_RUN"
        return fake_primary(base, token, video, work, job)
    monkeypatch.setattr(adapter, "_run_primary", overlapping)
    run_visual(first, tmp_path, "one")
    # The process-wide gate is released after the first pass.
    run_visual(second, tmp_path, "two")
    assert first.calls == second.calls == ["visual", "primary"]
    assert first.derive_deals_decisions([], "one")[0][-1]["deal_id"] == "first:one"
    assert second.derive_deals_decisions([], "two")[0][-1]["deal_id"] == "second:two"


def test_unusable_input_returns_unavailable_but_integrity_error_escapes(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "_prepare_profile_seed", lambda *args: (tmp_path/"ref", tmp_path/"profile", {}, {}))
    monkeypatch.setattr(adapter, "_prepare_assets", lambda *args: tmp_path)
    def unavailable(*args, **kwargs):
        raise adapter.PrimaryVideoInputError("video cannot be opened")
    monkeypatch.setattr(adapter, "recognize_video_primary", unavailable)
    deals, shots, qc = adapter._run_primary(None, "unused", tmp_path/"video", tmp_path, "job")
    assert deals == shots == [] and qc["status"] == "UNAVAILABLE"
    def broken(*args, **kwargs):
        from bridge_vision.bridgit_primary_video_candidate import PrimaryVideoRecognitionError
        raise PrimaryVideoRecognitionError("reference integrity mismatch")
    monkeypatch.setattr(adapter, "recognize_video_primary", broken)
    with pytest.raises(ValueError, match="reference integrity"):
        adapter._run_primary(None, "unused", tmp_path/"video", tmp_path, "job")


def test_candidate_selector_ignores_historical_global_retry_patch(monkeypatch):
    np = pytest.importorskip("numpy")
    from bridge_vision.bridgit_event_frame_selector import EventFrameSelector
    from bridge_vision.bridgit_candidate_frame_selector import CandidateFrameSelector
    original = EventFrameSelector.observe
    def old_retry(self, signature, timestamp):
        event = original(self, signature, timestamp)
        if event is not None and event.reason != "WATCHDOG_STABLE_STATE":
            self.schedule_retry(timestamp, delay_ms=1500)
        return event
    monkeypatch.setattr(EventFrameSelector, "observe", old_retry)
    old, candidate = EventFrameSelector(watchdog_ms=180000), CandidateFrameSelector(watchdog_ms=180000)
    signature = np.zeros((16, 16), dtype=np.uint8)
    for selector in (old, candidate):
        assert selector.observe(signature, 0) is None
        assert selector.observe(signature, 1000).reason == "INITIAL_STABLE_STATE"
    assert old.observe(signature, 3000) is not None
    assert candidate.observe(signature, 3000) is None
    candidate.schedule_retry(3000)
    assert candidate.observe(signature, 5000).reason == "WATCHDOG_STABLE_STATE"


def test_candidate_selector_preserves_frozen_event_semantics():
    np = pytest.importorskip("numpy")
    from bridge_vision.bridgit_event_frame_selector import EventFrameSelector
    from bridge_vision.bridgit_candidate_frame_selector import CandidateFrameSelector
    old, candidate = EventFrameSelector(watchdog_ms=6000), CandidateFrameSelector(watchdog_ms=6000)
    for timestamp, state in enumerate([0]*10 + [80]*5 + [0]*3 + [120]*12):
        signature = np.full((16, 16), state, dtype=np.uint8)
        assert old.observe(signature, timestamp*1000) == candidate.observe(signature, timestamp*1000)


def test_cold_candidate_import_does_not_load_v3_and_survives_prior_selector_patch():
    script = r'''
import sys
from bridge_vision.bridgit_event_frame_selector import EventFrameSelector
def forbidden(*args):
    raise AssertionError("historical selector called")
EventFrameSelector.observe = forbidden
from bridge_vision import bridgit_primary_production_candidate
from bridge_vision.bridgit_candidate_frame_selector import CandidateFrameSelector
assert CandidateFrameSelector.observe is not EventFrameSelector.observe
assert not any(name in sys.modules for name in (
    "bridge_vision.bridgit_primary_production_r264",
    "bridge_vision.bridgit_primary_production_r265",
    "bridge_vision.bridgit_primary_video_r264",
    "bridge_vision.bridgit_rank_layout_r264",
    "bridge_runtime_hardening_r26_4", "bridge_runtime_hardening_r26_5",
))
print("ISOLATED_V2_IMPORT_PASS")
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ISOLATED_V2_IMPORT_PASS"


@pytest.fixture
def runtime_ports(monkeypatch):
    import bridge_runtime_hardening_r26_candidate as runtime
    base, calls = make_base(), []
    def previous_install(token_func):
        assert runtime.os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] == "synthetic-previous"
        calls.append("previous")
    previous = SimpleNamespace(REVISION="synthetic-previous", install=previous_install)
    semantic = SimpleNamespace(previous=SimpleNamespace())
    monkeypatch.setattr(runtime, "base", base)
    monkeypatch.setattr(runtime, "core", SimpleNamespace())
    monkeypatch.setattr(runtime, "previous", previous)
    monkeypatch.setattr(runtime, "_INSTALL_STATE", "NOT_INSTALLED")
    monkeypatch.setattr(runtime, "_TOKEN_FUNC", None)
    monkeypatch.setitem(sys.modules, "run_master_3_1_free_semantic_v2", semantic)
    monkeypatch.setenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", runtime.REVISION)
    return runtime, base, calls, semantic


@pytest.mark.parametrize("revision", ["", "3.1-free-r26.3", "3.1-free-r26.4", "3.1-free-r26.5"])
def test_runtime_is_off_without_exact_candidate_revision(runtime_ports, monkeypatch, revision):
    runtime, base, calls, _ = runtime_ports
    monkeypatch.setenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", revision)
    with pytest.raises(RuntimeError, match="exact requested revision"):
        runtime.install(TOKEN)
    assert calls == [] and not hasattr(base, adapter.STATE_ATTRIBUTE)


def test_explicit_runtime_installs_predecessor_and_one_v2_adapter(runtime_ports, monkeypatch, tmp_path):
    runtime, base, calls, semantic = runtime_ports
    monkeypatch.setattr(adapter, "_run_primary", fake_primary)
    runtime.install(TOKEN)
    runtime.install(TOKEN)
    assert calls == ["previous"]
    run_visual(base, tmp_path)
    assert base.calls == ["visual", "primary"]
    assert base.ALGORITHM_REVISION == semantic.REVISION == runtime.REVISION
    assert runtime.PRODUCTION_ALLOWED is False
    assert not hasattr(runtime, "run")
    assert runtime.os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] == runtime.REVISION


def test_failed_runtime_install_restores_requested_revision_and_refuses_retry(runtime_ports, monkeypatch):
    runtime, base, _, _ = runtime_ports
    def broken(*args):
        raise RuntimeError("synthetic inherited failure")
    monkeypatch.setattr(runtime.previous, "install", broken)
    with pytest.raises(RuntimeError, match="inherited failure"):
        runtime.install(TOKEN)
    assert runtime.os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] == runtime.REVISION
    assert not hasattr(base, adapter.STATE_ATTRIBUTE)
    with pytest.raises(RuntimeError, match="fresh process"):
        runtime.install(TOKEN)


def test_runtime_rejects_old_primary_before_inherited_install(runtime_ports, monkeypatch):
    runtime, base, calls, _ = runtime_ports
    monkeypatch.setitem(sys.modules, adapter.HISTORICAL_ADAPTERS[0], SimpleNamespace(_INSTALLED_BASE_IDS={id(base)}))
    with pytest.raises(adapter.CandidateInstallationError, match="another primary"):
        runtime.install(TOKEN)
    assert calls == []


def test_runtime_reinstall_refuses_new_token_provider(runtime_ports):
    runtime, _, _, _ = runtime_ports
    runtime.install(TOKEN)
    with pytest.raises(RuntimeError, match="token provider"):
        runtime.install(lambda: "other")
