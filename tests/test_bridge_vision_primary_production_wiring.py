from pathlib import Path
import bridge_runtime_hardening_r26 as runtime
import run_master_3_1_free as master

def test_r26_installs_primary_visual_hooks(monkeypatch):
    monkeypatch.setattr(runtime.previous, 'install', lambda token_func: None)
    before_visual = master.visual
    runtime.install(lambda: 'token')
    assert master.visual is not before_visual
    assert runtime.REVISION == '3.1-free-r26.2'
    assert master.ALGORITHM_REVISION == '3.1-free-r26.2'

def test_primary_contract_is_fail_closed_in_source():
    source = Path('bridge_vision/bridgit_primary_production.py').read_text(encoding='utf-8')
    assert 'VISUAL_PRIMARY_RECOGNIZED' in source
    assert 'VISUAL_ONLY; NO_DECK_COMPLEMENT' in source
    assert 'canonical_promotion_allowed' in source
    assert 'APPROVED_GAMBLER_CLASSIC_SPRITE_SHA256' in source


def test_r261_geometry_gate_wraps_native_gambler_context(monkeypatch):
    import contextlib
    from bridge_vision import bridgit_primary_video as primary
    active = {"value": False}
    @contextlib.contextmanager
    def ctx():
        active["value"] = True
        try: yield
        finally: active["value"] = False
    original = primary._full_geometry_gate
    monkeypatch.setattr(runtime.previous, 'install', lambda token_func: None)
    monkeypatch.setattr(runtime, 'native_gambler_geometry', ctx)
    monkeypatch.setattr(primary, '_full_geometry_gate', lambda image, bank, profile: active["value"])
    runtime._INSTALLED_BASE_IDS = getattr(runtime, '_INSTALLED_BASE_IDS', set())
    runtime.install(lambda: 'token')
    assert primary._full_geometry_gate(None, None, None) is True


def test_r262_selector_schedules_one_settle_retry(monkeypatch):
    from bridge_vision import bridgit_primary_video as primary
    monkeypatch.setattr(runtime.previous, 'install', lambda token_func: None)
    runtime.install(lambda: 'token')
    assert getattr(primary.EventFrameSelector.observe, '_r262_settle_retry', False) is True
