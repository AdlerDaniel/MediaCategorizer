"""Console-only regression fixture; run after packaging with --onefile."""
import json
import os
from pathlib import Path
import subprocess
import sys
from media_categorizer.update_launch import independent_environment, external_dll_search

if sys.argv[1] == '--child':
    print('RESTART_OK', flush=True)
else:
    shell = Path(os.environ['SystemRoot'])/'System32/cmd.exe'
    report = {}
    for name, environment in [('inherited', dict(os.environ)), ('independent', independent_environment())]:
        with external_dll_search():
            result = subprocess.run([str(shell), '/d', '/c', sys.executable, '--child'],
                                    env=environment, capture_output=True, text=True, timeout=30,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        report[name] = dict(code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2), encoding='utf-8')
    assert report['inherited']['code'] != 0 and 'parent process has different executable' in report['inherited']['stderr'], report
    assert report['independent']['code'] == 0 and 'RESTART_OK' in report['independent']['stdout'], report
    print('Frozen EXE regression reproduced; independent restart succeeds')
