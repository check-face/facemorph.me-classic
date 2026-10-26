"""The README-is-the-test harness's own logic: block extraction, the only allowed substitutions, JPEG sizing."""
import struct
import tempfile
import unittest
from pathlib import Path

import readme_test

README = Path(__file__).resolve().parent / 'README.md'


class ExtractTests(unittest.TestCase):
    def test_published_readme_has_both_marked_blocks(self):
        quickstart = readme_test.extract_block(README, 'readme-quickstart')
        cpu = readme_test.extract_block(README, 'readme-cpu')
        for block in (quickstart, cpu):
            self.assertIn(f'git clone --branch self-host {readme_test.CANONICAL_REPO} facemorph.me', block)
            self.assertIn('cd facemorph.me', block)
        self.assertEqual(quickstart.strip().splitlines()[-1], 'ACCEPT_MODEL_TERMS=yes docker compose up')
        self.assertEqual(cpu.strip().splitlines()[-1], 'ACCEPT_MODEL_TERMS=yes docker compose -f self-host/compose.yml up')

    def test_missing_or_unclosed_marker_refuses(self):
        with tempfile.NamedTemporaryFile('w', suffix='.md') as f:
            f.write('<!-- readme-cpu: begin -->\n\n```sh\necho hi\n```\n')
            f.flush()
            with self.assertRaises(SystemExit):
                readme_test.extract_block(f.name, 'readme-cpu')
            with self.assertRaises(SystemExit):
                readme_test.extract_block(f.name, 'readme-quickstart')


class SubstituteTests(unittest.TestCase):
    block = f'git clone --branch self-host {readme_test.CANONICAL_REPO} facemorph.me\ncd facemorph.me\nACCEPT_MODEL_TERMS=yes docker compose up\n'

    def test_canonical_run_changes_nothing(self):
        self.assertEqual(readme_test.substitute(self.block, None, None), (self.block, []))
        self.assertEqual(readme_test.substitute(self.block, readme_test.CANONICAL_REPO, 'self-host'), (self.block, []))

    def test_development_run_changes_only_repository_and_branch(self):
        script, changes = readme_test.substitute(self.block, '/tmp/dev.git', 'candidate')
        self.assertEqual(script, self.block.replace(readme_test.CANONICAL_REPO, '/tmp/dev.git')
                         .replace('--branch self-host ', '--branch candidate '))
        self.assertEqual(len(changes), 2)
        self.assertEqual(script.splitlines()[1:], self.block.splitlines()[1:])

    def test_block_without_the_canonical_clone_refuses(self):
        with self.assertRaises(SystemExit):
            readme_test.substitute('docker compose up\n', '/tmp/dev.git', None)


class JpegTests(unittest.TestCase):
    def test_reads_baseline_sof(self):
        sof = b'\xff\xc0' + struct.pack('>HBHH', 11, 8, 512, 640) + b'\x01\x01\x11\x00'
        data = b'\xff\xd8' + b'\xff\xe0' + struct.pack('>H', 4) + b'JF' + sof
        self.assertEqual(readme_test.jpeg_size(data), (640, 512))

    def test_not_a_jpeg(self):
        self.assertIsNone(readme_test.jpeg_size(b'\x89PNG\r\n'))


if __name__ == '__main__':
    unittest.main()
