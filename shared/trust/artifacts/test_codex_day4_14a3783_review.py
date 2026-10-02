"""Independent evidence-footer acceptance probes; submitted evidence is untouched."""
import hashlib
import pytest
from scripts import run_walk_forward_simulation as runner

@pytest.mark.parametrize('mode', ['success', 'failure', 'tampered', 'missing'])
def test_footer_uses_actual_sealed_evidence(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(runner, 'ROOT_DIR', tmp_path)
    art = tmp_path / 'shared/trust/artifacts'
    art.mkdir(parents=True)
    log = art / 'DAY4-BACKTEST-STRESS-TESTS.log'
    if mode != 'missing':
        content = ('EXIT_CODE: 0\n=== 7 passed in 0.12s ===\n' if mode == 'success'
                   else 'EXIT_CODE: 1\n=== 1 failed, 6 passed in 0.12s ===\n')
        log.write_text(content, encoding='utf-8')
        seal = hashlib.sha256(log.read_bytes()).hexdigest()
        log.with_suffix('.log.sha256').write_text(seal, encoding='utf-8')
        if mode == 'tampered':
            log.write_text(content + 'altered', encoding='utf-8')
    footer = runner.derive_verification_footer()
    if mode == 'success':
        assert '7 passed in 0.12s (Exit code: 0)' in footer
    elif mode == 'failure':
        assert '1 failed, 6 passed in 0.12s (Exit code: 1)' in footer
        assert '(Exit code: 0)' not in footer
    elif mode == 'tampered':
        assert 'MISMATCH' in footer
        assert 'Validated Test Execution' not in footer
    else:
        assert 'Pending recording' in footer
        assert 'Validated Test Execution' not in footer
