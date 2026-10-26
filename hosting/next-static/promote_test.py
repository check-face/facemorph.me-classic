#!/usr/bin/env python3
"""promote.py reads its source repository and runs by numeric ID, never by a name that can move.

After the organization rename the old owner/name of this repository resolved to the new monorepo,
so a name-based lookup would have read (and downloaded from) the wrong repository. A fake `gh` on
PATH answers the API reads; `--source-only` stops before any download, staging or publication.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROMOTE = Path(__file__).resolve().parent / 'promote.py'
CLASSIC = 295956585
MONOREPO = 1413979666

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
answers = json.loads(open(os.environ['FAKE_GH_ANSWERS']).read())
with open(os.environ['FAKE_GH_LOG'], 'a') as log: log.write(json.dumps(sys.argv[1:]) + '\n')
if sys.argv[1] != 'api' or sys.argv[2] not in answers:
    sys.stderr.write('unexpected gh call: %r\n' % (sys.argv[1:],)); sys.exit(1)
print(json.dumps(answers[sys.argv[2]]))
'''


def run(build_repo=CLASSIC, full_name='facemorph-me/facemorph.me-classic', path='.github/workflows/next-site.yml'):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'gh').write_text(FAKE_GH)
        (tmp / 'gh').chmod(0o755)
        repo = {'id': build_repo, 'full_name': full_name}
        answers = {
            f'repositories/{CLASSIC}': {'id': CLASSIC, 'full_name': full_name},
            f'repositories/{CLASSIC}/actions/runs/7': {
                'id': 7, 'conclusion': 'success', 'path': path, 'head_sha': 'a' * 40,
                'repository': repo, 'head_repository': repo,
            },
        }
        (tmp / 'answers.json').write_text(json.dumps(answers))
        env = {**os.environ, 'PATH': f'{tmp}{os.pathsep}{os.environ["PATH"]}',
               'FAKE_GH_ANSWERS': str(tmp / 'answers.json'), 'FAKE_GH_LOG': str(tmp / 'log.jsonl')}
        out = subprocess.run([sys.executable, str(PROMOTE), '--build-run', '7', '--qualification-run', '7', '--source-only'],
                             env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in (tmp / 'log.jsonl').read_text().splitlines()] if (tmp / 'log.jsonl').exists() else []
        return out, calls


class PromoteSourceTest(unittest.TestCase):
    def test_source_only_reads_the_classic_repository_by_id(self):
        out, calls = run()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), {
            'repositoryId': CLASSIC, 'repository': 'facemorph-me/facemorph.me-classic',
            'buildRun': 7, 'qualificationRun': 7, 'source': 'a' * 40, 'published': False})
        self.assertEqual(calls, [['api', f'repositories/{CLASSIC}'], ['api', f'repositories/{CLASSIC}/actions/runs/7']])
        self.assertFalse(any('repos/' in ' '.join(c) for c in calls), 'no lookup by owner/name')

    def test_a_run_from_another_repository_is_refused(self):
        out, _ = run(build_repo=MONOREPO)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('Required successful workflow/source is missing', out.stderr)

    def test_an_id_that_resolves_to_another_name_is_refused(self):
        out, calls = run(full_name='facemorph-me/facemorph.me')
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('not facemorph-me/facemorph.me-classic', out.stderr)
        self.assertEqual(len(calls), 1, 'stops before reading any run')

    def test_a_different_workflow_is_refused(self):
        out, _ = run(path='.github/workflows/other.yml')
        self.assertNotEqual(out.returncode, 0)

    def test_publication_inputs_are_still_required_without_source_only(self):
        out = subprocess.run([sys.executable, str(PROMOTE), '--build-run', '7', '--qualification-run', '7'],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn('--runtime, --catalogue and --work are required', out.stderr)


if __name__ == '__main__':
    unittest.main()
