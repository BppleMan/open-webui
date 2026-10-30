#!/usr/bin/env python3
"""Reject new diagnostics against the documented upstream v0.12.5 baseline."""

import collections
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'scripts/oi/upstream-type-diagnostics.json'


def diagnostics(text):
    # GitHub Actions enables colored svelte-check output even when stdout is piped.
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    result = collections.Counter()
    path = None
    for line in text.splitlines():
        match = re.match(r'(.+?):\d+:\d+$', line)
        if match:
            path = match[1].replace('\\', '/')
            for marker in ('/src/', '/scripts/'):
                if marker in path:
                    path = path[path.index(marker) + 1 :]
                    break
            else:
                path = pathlib.Path(path).name
        elif path and (line.startswith('Error:') or line.startswith('Warn:')):
            line = re.sub(r'[^ \'"]*/node_modules/', '<dependencies>/', line)
            result[path + '\t' + line.strip()] += 1
    return result


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--baseline':
        value = diagnostics(pathlib.Path(sys.argv[2]).read_text())
        BASELINE.write_text(json.dumps(dict(sorted(value.items())), ensure_ascii=False, indent=2) + '\n')
        print(f'Recorded {sum(value.values())} existing upstream diagnostics')
        sys.exit(0)
    if not (ROOT / '.svelte-kit/tsconfig.json').exists():
        subprocess.run(['npx', 'svelte-kit', 'sync'], cwd=ROOT, check=True)
    run = subprocess.run(
        ['npx', 'svelte-check', '--tsconfig', './tsconfig.json'],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    current = diagnostics(run.stdout)
    allowed = collections.Counter(json.loads(BASELINE.read_text()))
    new = current - allowed
    if new or (run.returncode and not current):
        for message, count in sorted(new.items()):
            print(f'{count} new: {message}')
        if not current:
            print(run.stdout)
        sys.exit(1)
    print(f'No new type/a11y diagnostics; {sum(current.values())} upstream diagnostics remain (not a clean typecheck).')
