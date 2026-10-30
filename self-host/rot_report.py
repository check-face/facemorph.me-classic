#!/usr/bin/env python3
"""Issue text for a failed self-host rot check (fm-fy2.16): the failed assertions and the pinned inputs involved.

  python3 self-host/rot_report.py <evidence dir> '<needs JSON>' <run URL>

Reads each downloaded readme-test-* artifact's result.json and logs. For every failed assertion it names the
assertion and, from the logs, the pinned input the failure points at: a model checksum, a base-image digest, a
hash-locked wheel, the NVlabs source archive, or an upstream host that no longer answers. Stdlib only.
"""
import json
import re
import sys
from pathlib import Path

# Log lines that name a pinned input, and what that input is.
PINNED = [
    (r'Official download (?:differs from pinned source|did not match deployed checksum)[^\n]*', 'model checksum pin'),
    (r'Existing model checksum mismatch[^\n]*|Unverified encoder asset: [^\n]*', 'model file on disk vs its pin'),
    (r'[^\n]*(?:THESE PACKAGES DO NOT MATCH THE HASHES|Hashes are required|No matching distribution)[^\n]*',
     'hash-locked Python dependency (requirements-*.lock)'),
    (r'[^\n]*(?:manifest unknown|not found: manifest|failed to resolve source metadata)[^\n]*',
     'base image pinned by digest'),
    (r'[^\n]*stylegan\.tar\.gz: FAILED[^\n]*|[^\n]*stylegan2-ada-pytorch/archive[^\n]*HTTP Error[^\n]*',
     'NVlabs stylegan2-ada-pytorch archive pin'),
    (r'[^\n]*(?:HTTP Error 404|HTTP Error 403|Name or service not known|urlopen error)[^\n]*',
     'upstream host (a dead weight link is reported, not worked around)'),
]


def pinned_inputs(cell_dir):
    found, seen = [], set()
    for log in sorted(Path(cell_dir).glob('*.log')):
        text = log.read_text(errors='replace')
        for pattern, what in PINNED:
            for m in re.finditer(pattern, text):
                line = m.group(0).strip()[:300]
                if '{' in line or line.lstrip().startswith('raise '):
                    continue  # a traceback's source line, not the failure it reports
                line = re.sub(r'^[\w.-]+-\d+\s+\|\s+', '', line)  # drop the Compose service prefix
                if 'the host sent a' in line:
                    what = 'upstream weight host served a page, not the model (reported, not worked around)'
                if (what, line) not in seen:
                    seen.add((what, line))
                    found.append((what, line, log.name))
    return found[:12]


def report(evidence, needs, run):
    lines = [f'The self-host README rot check failed: {run}', '']
    failed_jobs = [f'{k}: {v["result"]}' for k, v in needs.items() if v['result'] not in ('success', 'skipped')]
    lines += ['**Failed jobs:** ' + (', '.join(failed_jobs) or 'none recorded'), '']
    cells = sorted(p for p in Path(evidence).glob('readme-test-*') if p.is_dir())
    for cell in cells:
        result_file = cell / 'result.json'
        if not result_file.exists():
            lines += [f'### {cell.name}', 'No result.json: the harness did not start (see the job log).', '']
            continue
        result = json.loads(result_file.read_text())
        failed = [a for a in result.get('assertions', []) if not a.get('passed')]
        if not failed:
            continue
        env = result.get('environment', {})
        lines += [f'### {cell.name}',
                  f'Commit `{result.get("commit", "?")}`, {result.get("run_kind", "?")} run, '
                  f'{env.get("host", "?")}, GPU {env.get("nvidia_smi", "n/a")}, '
                  f'arch list {result.get("torch", {}).get("arch_list", "n/a")}.', '',
                  '**Failed assertions:**']
        for a in failed:
            evidence_keys = {k: v for k, v in a.items() if k not in ('name', 'passed', 'seconds')}
            lines.append(f'- {a["name"]}: `{json.dumps(evidence_keys)[:400]}`')
        inputs = pinned_inputs(cell)
        lines += ['', '**Pinned inputs involved:**']
        lines += [f'- {what}: `{line}` ({log})' for what, line, log in inputs] or [
            '- none named in the logs; read the failed assertion and its log']
        lines.append('')
    lines += ['Owner: classic distribution workstream. Restore the pinned input where it is still obtainable; if it is '
              'gone for good, make only the minimal re-pin behind the same README-executed acceptance and state it in '
              'the README matrix. A dead upstream weight link is reported as such, not worked around. No general '
              'dependency bump.', '', 'Evidence: the readme-test-* artifacts of the run.']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    sys.stdout.write(report(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]))
