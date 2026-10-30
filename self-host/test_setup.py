"""fm-6bj unit tests for the setup gate and the download-verify primitives.

Everything here runs against the real thing: a real local HTTP server serving real
bytes, real files in a real temporary directory, and the real setup.py as a subprocess.
No fakes, no mocks, no network stubs.
"""
import hashlib
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import prepare_encoder
import prepare_model
import setup

BODY = b'original checkpoint bytes for the fm-6bj fixture' * 8
BODY_SHA = hashlib.sha256(BODY).hexdigest()
OTHER = b'different bytes entirely' * 8


class FixtureServer:
    """A real HTTP server; `mode` decides what GET serves (healthy, truncated, wrong)."""

    def __init__(self):
        self.requests = 0
        self.mode = 'healthy'

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server = self.server.attachments
                server.requests += 1
                if server.mode == 'truncated':
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY[: len(BODY) // 2])
                    self.close_connection = True
                    return
                if server.mode == 'quota':
                    page = b'<!DOCTYPE html><html><head><title>Google Drive - Quota exceeded</title></head></html>'
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                body = BODY if server.mode == 'healthy' else OTHER
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.attachments = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}/asset'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class TermsGate(unittest.TestCase):
    def test_missing_acknowledgement_is_refused_with_terms_location_and_command(self):
        env = {k: v for k, v in os.environ.items() if k != setup.TERMS_ENV}
        env['PATH'] = os.environ['PATH']
        result = subprocess.run(
            [sys.executable, str(Path(setup.__file__).resolve())],
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('self-host/README.md', result.stderr)
        self.assertIn('ACCEPT_MODEL_TERMS=yes docker compose up', result.stderr)

    def test_wrong_value_is_still_refused(self):
        self.assertFalse(setup.acknowledged([], {setup.TERMS_ENV: 'no'}))
        self.assertFalse(setup.acknowledged([], {setup.TERMS_ENV: ''}))

    def test_environment_or_flag_acknowledges(self):
        self.assertTrue(setup.acknowledged([], {setup.TERMS_ENV: 'yes'}))
        self.assertTrue(setup.acknowledged([], {setup.TERMS_ENV: 'YES'}))
        self.assertTrue(setup.acknowledged([setup.ACCEPT_FLAG], {}))
        self.assertFalse(setup.acknowledged([], {}))


class FetchVerified(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer()
        self.directory = Path(tempfile.mkdtemp())
        self.target = self.directory / 'asset.bin'

    def tearDown(self):
        self.server.stop()

    def test_download_then_reuse_without_requests(self):
        downloaded = prepare_model.fetch_verified(self.server.url, BODY_SHA, len(BODY), self.target)
        self.assertEqual(downloaded, len(BODY))
        self.assertEqual(self.target.read_bytes(), BODY)
        requests_after_download = self.server.requests
        reused = prepare_model.fetch_verified(self.server.url, BODY_SHA, len(BODY), self.target)
        self.assertEqual(reused, 0, 'a verified file is reused, not re-downloaded')
        self.assertEqual(self.server.requests, requests_after_download)

    def test_wrong_sha256_is_rejected_and_nothing_is_installed(self):
        with self.assertRaises(RuntimeError):
            prepare_model.fetch_verified(self.server.url, '0' * 64, len(BODY), self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), [], 'no partial file is left behind')

    def test_truncated_download_is_rejected_and_replaced_on_retry(self):
        self.server.mode = 'truncated'
        with self.assertRaises(RuntimeError):
            prepare_model.fetch_verified(self.server.url, BODY_SHA, len(BODY), self.target)
        self.assertFalse(self.target.exists())
        self.server.mode = 'healthy'
        downloaded = prepare_model.fetch_verified(self.server.url, BODY_SHA, len(BODY), self.target)
        self.assertEqual(downloaded, len(BODY))
        self.assertEqual(self.target.read_bytes(), BODY)

    def test_oversized_download_is_rejected(self):
        with self.assertRaises(RuntimeError):
            prepare_model.fetch_verified(self.server.url, BODY_SHA, len(BODY) - 1, self.target)
        self.assertFalse(self.target.exists())

    def test_corrupt_existing_file_is_refused_and_never_overwritten(self):
        self.target.write_bytes(OTHER)
        with self.assertRaises(SystemExit):
            prepare_model.fetch_verified(self.server.url, BODY_SHA, len(BODY), self.target)
        self.assertEqual(self.target.read_bytes(), OTHER)
        self.assertEqual(self.server.requests, 0, 'a refused file is not re-downloaded over')

    def test_encoder_download_entry_shares_the_contract(self):
        entry = {'url': self.server.url, 'sha256': BODY_SHA, 'bytes': len(BODY)}
        downloaded = prepare_encoder.download_entry(entry, 'asset.bin', self.directory)
        self.assertEqual(downloaded, len(BODY))
        reused = prepare_encoder.download_entry(entry, 'asset.bin', self.directory)
        self.assertEqual(reused, 0)

    def test_encoder_wrong_checksum_is_rejected(self):
        entry = {'url': self.server.url, 'sha256': 'f' * 64, 'bytes': len(BODY)}
        with self.assertRaises(ValueError):
            prepare_encoder.download_entry(entry, 'asset.bin', self.directory)
        self.assertFalse(self.target.exists())


    def test_a_quota_page_is_rejected_and_named(self):
        self.server.mode = 'quota'
        entry = {'url': self.server.url, 'sha256': BODY_SHA, 'bytes': len(BODY), 'compression': 'bz2'}
        with self.assertRaisesRegex(ValueError, 'did not match deployed checksum: asset.bin .the host sent a '
                                    r'\d+-byte text/html page: "Google Drive - Quota exceeded", not the model'):
            prepare_encoder.download_entry(entry, 'asset.bin', self.directory)
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
