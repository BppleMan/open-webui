import importlib.util
from pathlib import Path


def test_type_gate_parses_ci_ansi_diagnostics_without_losing_errors():
    path = Path(__file__).resolve().parents[2] / 'scripts/oi/check-types.py'
    spec = importlib.util.spec_from_file_location('oi_type_gate', path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    plain = '/home/runner/work/app/src/lib/example.svelte:10:2\nError: Type mismatch. (ts)\n'
    colored = (
        '\x1b[1m/home/runner/work/app/src/lib/example.svelte:10:2\x1b[0m\n\x1b[31mError: Type mismatch. (ts)\x1b[0m\n'
    )
    expected = {'src/lib/example.svelte\tError: Type mismatch. (ts)': 1}
    assert gate.diagnostics(plain) == gate.diagnostics(colored) == expected
