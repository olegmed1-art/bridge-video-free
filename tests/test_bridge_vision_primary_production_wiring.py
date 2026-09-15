from pathlib import Path
import bridge_runtime_hardening_r26 as runtime
import run_master_3_1_free as master

def test_r26_installs_primary_visual_hooks(monkeypatch):
    monkeypatch.setattr(runtime.previous, 'install', lambda token_func: None)
    before_visual = master.visual
    runtime.install(lambda: 'token')
    assert master.visual is not before_visual
    assert runtime.REVISION == '3.1-free-r26'
    assert master.ALGORITHM_REVISION == '3.1-free-r26'

def test_primary_contract_is_fail_closed_in_source():
    source = Path('bridge_vision/bridgit_primary_production.py').read_text(encoding='utf-8')
    assert 'VISUAL_PRIMARY_RECOGNIZED' in source
    assert 'VISUAL_ONLY; NO_DECK_COMPLEMENT' in source
    assert 'canonical_promotion_allowed' in source
    assert 'APPROVED_GAMBLER_CLASSIC_SPRITE_SHA256' in source
