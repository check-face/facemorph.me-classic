import importlib.util, io, json, pathlib, tempfile, unittest
from contextlib import redirect_stdout

spec = importlib.util.spec_from_file_location('check_pins', pathlib.Path(__file__).with_name('check-pins.py'))
check_pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_pins)

DIGEST = '@sha256:' + 'ab' * 32


class CheckPins(unittest.TestCase):
    def run_on(self, files):
        with tempfile.TemporaryDirectory() as root:
            for name, text in files.items():
                path = pathlib.Path(root, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            out = io.StringIO()
            with redirect_stdout(out):
                status = check_pins.main([root])
            return status, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_floating_tag_fails(self):
        status, lines = self.run_on({'Dockerfile': 'FROM node:22-bookworm-slim\n'})
        self.assertEqual(status, 1)
        self.assertEqual(lines[0]['image'], 'node:22-bookworm-slim')
        self.assertEqual(lines[0]['line'], 1)

    def test_digest_passes(self):
        status, lines = self.run_on({'Dockerfile': f'FROM node:22-bookworm-slim{DIGEST}\n'})
        self.assertEqual(status, 0)
        self.assertEqual(lines, [{'summary': {'files': 1, 'findings': 0}}])

    def test_stages_platform_and_scratch_are_not_pulls(self):
        dockerfile = (f'FROM --platform=$BUILDPLATFORM python:3.11-slim{DIGEST} AS deps\n'
                      'FROM deps AS api\nFROM scratch\nFROM api AS runtime\n')
        self.assertEqual(self.run_on({'Dockerfile': dockerfile})[0], 0)

    def test_variable_reference_and_untagged_image_fail(self):
        status, lines = self.run_on({'Dockerfile': f'FROM node:${{VERSION}}{DIGEST}\nFROM ubuntu\n'})
        self.assertEqual(status, 1)
        self.assertEqual([l['line'] for l in lines[:-1]], [1, 2])

    def test_compose_images(self):
        status, lines = self.run_on({
            'compose.yml': f'services:\n  db:\n    image: mongo:8.0.13\n  ok:\n    image: "mongo:8.0.13{DIGEST}"\n',
            'compose.gpu.yml': 'services:\n  api:\n    build: .\n',
            'notes.yml': 'image: not-a-compose-file\n',
        })
        self.assertEqual(status, 1)
        self.assertEqual([(pathlib.Path(l['file']).name, l['line']) for l in lines[:-1]], [('compose.yml', 3)])
        self.assertEqual(lines[-1]['summary']['files'], 2)

    def test_nothing_to_scan_fails(self):
        self.assertEqual(self.run_on({'README.md': 'no images\n'})[0], 1)


if __name__ == '__main__':
    unittest.main()
