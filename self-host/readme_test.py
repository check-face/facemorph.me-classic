#!/usr/bin/env python3
"""README-is-the-test harness for the classic self-host distribution (fm-fy2.16).

Extracts a marked block from self-host/README.md and runs it verbatim in an empty directory, with a fresh Compose
project (no existing containers or volumes) and, by default, a fresh BuildKit builder (no build cache), then checks
what the README promises. Modes:

  gpu         the NVIDIA quickstart block (readme-quickstart); CUDA serves, then the full assertion list
  cpu         the CPU block (readme-cpu); CPU serves, then the full assertion list
  gpu-absent  the quickstart block on a host without an NVIDIA runtime: it must fail clearly, never on CPU
  gpu-unsupported  the quickstart block on a GPU outside the wheel's kernels: the startup capability check stops it
                   with a plain message, then the CPU block must serve a face on the same host

The only permitted change to a block is the repository URL and branch (--repo, --branch), for a development run of
an unpublished commit; each substitution is logged and the result is labelled "development". The isolation is
environment only, also logged: COMPOSE_PROJECT_NAME, CHECKFACE_PORT and BUILDX_BUILDER.

Writes <out>/result.json (every assertion with its evidence and timing, the environment, image digests, first-run
duration and disk use) and <out>/*.log. Exit 0 only when every assertion for the mode passed. Python 3.10+, stdlib
only; needs git, docker with Compose v2.20+ and buildx, and for the frontend check a Chrome/Chromium (--chrome).
"""
import argparse
import json
import os
import platform
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANONICAL_REPO = 'https://github.com/facemorph-me/facemorph.me-classic.git'
CANONICAL_BRANCH = 'self-host'
MARKERS = {'gpu': 'readme-quickstart', 'gpu-absent': 'readme-quickstart', 'gpu-unsupported': 'readme-quickstart',
           'cpu': 'readme-cpu'}
MODEL = 'stylegan2-ffhq-config-f.pkl'
MODEL_BYTES = 381673535
# A small encoder asset for the corruption check (refused by hash, never overwritten).
CORRUPT_ASSET = 'shape_predictor_68_face_landmarks.dat'
SERVING = 'Serving on http://localhost:8080'


def extract_block(readme, marker):
    """The single fenced sh block between `<!-- <marker>: begin ... -->` and `<!-- <marker>: end -->`."""
    text = Path(readme).read_text()
    found = re.search(rf'<!-- {re.escape(marker)}: begin.*?-->\s*```sh\n(.*?)```\s*<!-- {re.escape(marker)}: end -->',
                      text, re.S)
    if not found:
        raise SystemExit(f'{readme}: no marked {marker} block')
    return found.group(1)


def substitute(block, repo, branch):
    """Apply the development substitutions; returns the block and a list of what changed."""
    changes = []
    if repo and repo != CANONICAL_REPO:
        if CANONICAL_REPO not in block:
            raise SystemExit(f'block does not name {CANONICAL_REPO}')
        block = block.replace(CANONICAL_REPO, repo)
        changes.append(f'repository {CANONICAL_REPO} -> {repo}')
    if branch and branch != CANONICAL_BRANCH:
        token = f'--branch {CANONICAL_BRANCH} '
        if token not in block:
            raise SystemExit(f'block does not contain "{token.strip()}"')
        block = block.replace(token, f'--branch {branch} ')
        changes.append(f'branch {CANONICAL_BRANCH} -> {branch}')
    return block, changes


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Run:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.stamp = time.strftime('%Y%m%dt%H%M%S').lower()
        self.project = f'readmetest-{args.mode}-{self.stamp}'
        self.port = args.port or free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        self.workdir = Path(tempfile.mkdtemp(prefix=f'{self.project}-', dir=args.workdir))
        self.checkout = None
        self.builder = None if args.reuse_build_cache else f'{self.project}-builder'
        self.env = {**os.environ, 'COMPOSE_PROJECT_NAME': self.project, 'CHECKFACE_PORT': str(self.port)}
        self.env.pop('ACCEPT_MODEL_TERMS', None)  # only the README's own commands acknowledge the terms
        self.env.pop('COMPOSE_FILE', None)
        if self.builder:
            self.env['BUILDX_BUILDER'] = self.builder
        self.result = {'harness': 'self-host/readme_test.py', 'mode': args.mode, 'project': self.project,
                       'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'assertions': []}
        self.failed = False

    # --- plumbing -------------------------------------------------------------------------------------------
    def log(self, message):
        line = f'[{time.strftime("%H:%M:%S")}] {message}'
        print(line, flush=True)
        with (self.out / 'harness.log').open('a') as f:
            f.write(line + '\n')

    def sh(self, command, cwd=None, env=None, timeout=600, check=False, log_name=None):
        """Run a command list or shell string; returns CompletedProcess with combined output."""
        shell = isinstance(command, str)
        r = subprocess.run(['bash', '-c', command] if shell else command, cwd=cwd or self.checkout or self.workdir,
                           env=env or self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=timeout)
        if log_name:
            (self.out / log_name).write_text(r.stdout)
        if check and r.returncode:
            raise RuntimeError(f'{command} exited {r.returncode}: {r.stdout[-2000:]}')
        return r

    def compose(self, *argv, timeout=600, **kw):
        files = ['-f', 'self-host/compose.yml'] if self.args.mode == 'cpu' else []
        return self.sh(['docker', 'compose', *files, *argv], timeout=timeout, **kw)

    def record(self, name, passed, started, **evidence):
        entry = {'name': name, 'passed': bool(passed), 'seconds': round(time.monotonic() - started, 1), **evidence}
        self.result['assertions'].append(entry)
        self.failed |= not passed
        self.log(f'{"PASS" if passed else "FAIL"} {name} ({entry["seconds"]} s) {json.dumps(evidence)[:400]}')
        self.save()
        return passed

    def save(self):
        (self.out / 'result.json').write_text(json.dumps(self.result, indent=2) + '\n')

    def get(self, path, timeout=600):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return r.status, r.headers.get_content_type(), r.read()

    def health(self):
        try:
            return json.loads(self.get('/healthz', timeout=10)[2])
        except (urllib.error.URLError, ConnectionError, OSError, ValueError):
            return None

    # --- the README block -----------------------------------------------------------------------------------
    def start_block(self, script, name, extra_env=None):
        """Start a README block (or a resumed command) in the background; output goes to <out>/<name>.log."""
        env = {**self.env, **(extra_env or {})}
        log = (self.out / f'{name}.log').open('w')
        return subprocess.Popen(['bash', '-euo', 'pipefail', '-c', script], cwd=self.workdir if name.startswith('quickstart')
                                else self.checkout, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True), self.out / f'{name}.log'

    def wait_healthy(self, process, log, limit):
        """Seconds until the API logs it is serving and /healthz answers, or None if the command ends first."""
        started = time.monotonic()
        while time.monotonic() - started < limit:
            if process.poll() is not None:
                return None
            if SERVING in log.read_text(errors='replace') and self.health():
                return round(time.monotonic() - started, 1)
            time.sleep(5)
        return None

    def interrupt(self, process):
        """Ctrl-C, as the README's stop instruction: SIGINT to the foreground compose, then wait for it."""
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    # --- environment ----------------------------------------------------------------------------------------
    def environment(self):
        env = {'host': socket.gethostname(), 'os': platform.platform(), 'arch': platform.machine(),
               'docker': self.sh(['docker', 'version', '--format', '{{.Server.Version}}']).stdout.strip(),
               'compose': self.sh(['docker', 'compose', 'version', '--short']).stdout.strip()}
        smi = shutil.which('nvidia-smi')
        if smi:
            q = self.sh([smi, '--query-gpu=name,driver_version,compute_cap,memory.total', '--format=csv,noheader'])
            env['nvidia_smi'] = q.stdout.strip() if q.returncode == 0 else f'unavailable: {q.stdout.strip()[:200]}'
        else:
            env['nvidia_smi'] = 'not installed'
        runtimes = self.sh(['docker', 'info', '--format', '{{json .Runtimes}}']).stdout
        env['docker_nvidia_runtime'] = 'nvidia' in runtimes
        return env

    def preflight(self):
        """The project must start empty: no containers, volumes or images under its name."""
        leftovers = {
            'containers': self.sh(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={self.project}']).stdout.split(),
            'volumes': self.sh(['docker', 'volume', 'ls', '-q', '--filter', f'label=com.docker.compose.project={self.project}']).stdout.split(),
            'images': self.sh(['docker', 'images', '-q', '--filter', f'reference={self.project}*']).stdout.split()}
        if any(leftovers.values()):
            raise SystemExit(f'project {self.project} is not empty: {leftovers}')
        root = self.sh(['docker', 'info', '--format', '{{.DockerRootDir}}']).stdout.strip() or '/'
        free = shutil.disk_usage(root if Path(root).exists() else '/').free / 2**30
        self.result['docker_root_free_gib'] = round(free, 1)
        if free < self.args.min_free_gib:
            raise SystemExit(f'{root} has {free:.1f} GiB free; this run needs {self.args.min_free_gib} GiB '
                             '(a fresh build cache, the loaded image and the models)')
        if self.builder:
            self.sh(['docker', 'buildx', 'create', '--name', self.builder, '--driver', 'docker-container'], check=True)
        return leftovers

    # --- assertions -----------------------------------------------------------------------------------------
    def first_run(self, script):
        t = time.monotonic()
        process, log = self.start_block(script, 'quickstart')
        healthy = self.wait_healthy(process, log, self.args.first_run_limit)
        self.checkout = self.workdir / 'facemorph.me'
        text = log.read_text(errors='replace')
        downloads = [int(n) for n in re.findall(r'downloaded (\d+) bytes', text)]
        self.result['first_run_seconds'] = healthy
        ok = healthy is not None and SERVING in text and sum(downloads) > MODEL_BYTES
        self.record('first run: build from empty cache, download and verify, healthy, URL logged', ok, t,
                    seconds_to_healthy=healthy, downloaded_bytes=sum(downloads), serving_logged=SERVING in text,
                    log='quickstart.log')
        return process if ok else None

    def provenance(self):
        rev = self.sh(['git', 'rev-parse', 'HEAD']).stdout.strip()
        images = {}
        for service in ('setup', 'api', 'db'):
            ref = self.compose('images', '-q', service).stdout.strip().splitlines()
            if ref:
                info = json.loads(self.sh(['docker', 'image', 'inspect', ref[0]]).stdout)[0]
                images[service] = {'id': info['Id'], 'repo_digests': info.get('RepoDigests', []),
                                   'size_bytes': info.get('Size')}
        self.result.update(commit=rev, images=images)

    def disk(self):
        sizes = {}
        for volume in ('models', 'database', 'cache'):
            r = self.sh(['docker', 'run', '--rm', '-v', f'{self.project}_{volume}:/v:ro', '--entrypoint', 'du',
                         self.result['images']['api']['id'], '-sb', '/v'])
            sizes[f'volume_{volume}_bytes'] = int(r.stdout.split()[0]) if r.returncode == 0 else None
        sizes['images_bytes'] = sum({i['id']: i['size_bytes'] or 0 for i in self.result['images'].values()}.values())
        if self.builder:
            du = self.sh(['docker', 'buildx', 'du', '--builder', self.builder]).stdout
            total = re.search(r'Total:\s+([\d.]+\s*\w+)', du)
            sizes['build_cache'] = total.group(1) if total else None
        self.result['disk'] = sizes

    def health_and_face(self, provider):
        t = time.monotonic()
        health = self.health() or {}
        ok = health.get('ready') is True and health.get('provider') == provider
        if provider == 'cuda':
            ok &= bool(health.get('gpu'))
        self.record(f'/healthz reports provider {provider}' + (' and the GPU name' if provider == 'cuda' else ''),
                    ok, t, health={k: health.get(k) for k in ('ready', 'provider', 'gpu', 'gpu_capability',
                                                              'cuda_version', 'torch_version')})
        t = time.monotonic()
        try:
            status, mime, body = self.get('/api/face/?seed=0&dim=512')
            size = jpeg_size(body) if mime == 'image/jpeg' else None
            self.record('/api/face/?seed=0&dim=512 returns a 512x512 image', status == 200 and size == (512, 512), t,
                        status=status, content_type=mime, bytes=len(body), size=size)
        except urllib.error.URLError as error:
            self.record('/api/face/?seed=0&dim=512 returns a 512x512 image', False, t, error=str(error))

    def frontend(self):
        t = time.monotonic()
        name = 'the original UI renders a face in a browser'
        chrome = self.args.chrome or shutil.which('chromium') or shutil.which('google-chrome') or shutil.which('chrome')
        if not chrome:
            return self.record(name, False, t, untested='no Chrome/Chromium; pass --chrome')
        profile = tempfile.mkdtemp(prefix='readmetest-chrome-')
        try:
            dom = subprocess.run([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', f'--user-data-dir={profile}',
                                  '--virtual-time-budget=60000', '--dump-dom', self.base + '/'],
                                 capture_output=True, text=True, timeout=300).stdout
            subprocess.run([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', f'--user-data-dir={profile}',
                            '--virtual-time-budget=60000', '--window-size=1280,1600',
                            f'--screenshot={self.out / "frontend.png"}', self.base + '/'],
                           capture_output=True, timeout=300)
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        (self.out / 'frontend-dom.html').write_text(dom)
        faces = sorted(set(re.findall(r'<img[^>]+src="([^"]*/api/face/[^"]*)"', dom)))
        checked = []
        for src in faces[:3]:
            url = src if src.startswith('http') else self.base + ('' if src.startswith('/') else '/') + src
            url = url.replace('&amp;', '&')
            try:
                with urllib.request.urlopen(url, timeout=600) as r:
                    body = r.read()
                checked.append({'src': src, 'status': r.status, 'type': r.headers.get_content_type(),
                                'size': jpeg_size(body) if body[:2] == b'\xff\xd8' else None})
            except urllib.error.URLError as error:
                checked.append({'src': src, 'error': str(error)})
        ok = bool(checked) and all(c.get('status') == 200 and c.get('type', '').startswith('image/') for c in checked)
        self.record(name, ok, t, face_images=checked, screenshot='frontend.png', dom='frontend-dom.html')

    def smokes(self, gpu):
        scripts = ['smoke_frontend.py', 'test_legacy_contract.py', 'smoke_legacy.py', 'smoke_encoder_http.py']
        if gpu:
            scripts.append('smoke_gpu.py')
        labels = {'smoke_frontend.py': 'original UI assets, SPA paths and local oEmbed',
                  'test_legacy_contract.py': 'old seed/text semantics and the deployed route and latent contract',
                  'smoke_legacy.py': 'photo, morph and media endpoints (faces, morph frames, GIF/WebP/MP4, previews)',
                  'smoke_encoder_http.py': 'photo upload, alignment and e4e encoding over HTTP',
                  'smoke_gpu.py': 'CUDA in the container and an uncached face over HTTP'}
        for script in scripts:
            t = time.monotonic()
            r = self.compose('exec', '-T', 'api', 'python', script, timeout=3600, log_name=f'{script}.log')
            self.record(f'{script}: {labels[script]}', r.returncode == 0, t, exit=r.returncode,
                        tail=r.stdout.strip().splitlines()[-1:] if r.stdout.strip() else [], log=f'{script}.log')

    def down_up(self, process, script_up, provider):
        """Ctrl-C, `docker compose down`, then the README command again: volumes reused, nothing re-downloaded."""
        t = time.monotonic()
        self.interrupt(process)
        down = self.compose('down', log_name='down.log')
        process, log = self.start_block(script_up, 'resume')
        healthy = self.wait_healthy(process, log, self.args.restart_limit)
        text = log.read_text(errors='replace')
        downloads = [int(n) for n in re.findall(r'downloaded (\d+) bytes', text)]
        reused = len(re.findall(r'reused verified', text))
        provider_ok = (self.health() or {}).get('provider') == provider
        ok = down.returncode == 0 and healthy is not None and not any(downloads) and reused >= 2 and provider_ok
        self.result['restart_seconds'] = healthy
        self.record(f'down then up reuses the volumes with no re-download and is healthy within '
                    f'{self.args.restart_limit} s', ok, t, seconds_to_healthy=healthy, downloads=downloads,
                    reused_verified=reused, log='resume.log')
        return process

    def missing_terms(self, process, script_up):
        """The same command without ACCEPT_MODEL_TERMS ends quickly, non-zero, with the refusal; the API never runs."""
        t = time.monotonic()
        self.interrupt(process)
        self.compose('down')
        bare = script_up.replace('ACCEPT_MODEL_TERMS=yes ', '')
        r = self.sh(f'timeout 300 {bare}', log_name='missing-terms.log', timeout=320)
        state = self.compose('ps', '-a', '--format', '{{.State}}', 'api').stdout.strip()
        ok = r.returncode not in (0, 124) and 'ACCEPT_MODEL_TERMS=yes docker compose up' in r.stdout and state != 'running'
        self.record('missing terms acknowledgement fails fast with the refusal and no API', ok, t, exit=r.returncode,
                    api_state=state or 'not created', command=bare, log='missing-terms.log')

    def setup_once(self, name, timeout=3600):
        """`docker compose run --rm setup` with the acknowledgement, as the README's managing commands would."""
        return self.sh(['docker', 'compose', *(['-f', 'self-host/compose.yml'] if self.args.mode == 'cpu' else []),
                        'run', '--rm', 'setup'], env={**self.env, 'ACCEPT_MODEL_TERMS': 'yes'}, timeout=timeout,
                       log_name=name)

    def in_models(self, command, name):
        return self.sh(['docker', 'compose', *(['-f', 'self-host/compose.yml'] if self.args.mode == 'cpu' else []),
                        'run', '--rm', '--no-deps', '--entrypoint', 'sh', 'setup', '-c', command],
                       env={**self.env, 'ACCEPT_MODEL_TERMS': 'yes'}, log_name=name)

    def kill_during_setup(self, script_up, provider):
        """Remove the generator checkpoint, kill setup mid-download, then the README command recovers."""
        t = time.monotonic()
        self.in_models(f'rm -f /models/{MODEL}', 'kill-prepare.log')
        files = ['docker', 'compose', *(['-f', 'self-host/compose.yml'] if self.args.mode == 'cpu' else [])]
        setup = subprocess.Popen([*files, 'run', '--rm', '--name', f'{self.project}-killed-setup', 'setup'],
                                 cwd=self.checkout, env={**self.env, 'ACCEPT_MODEL_TERMS': 'yes'},
                                 stdout=(self.out / 'kill-setup.log').open('w'), stderr=subprocess.STDOUT)
        partial, waited = 0, time.monotonic()
        while time.monotonic() - waited < 900 and setup.poll() is None:
            r = self.sh(['docker', 'exec', f'{self.project}-killed-setup', 'sh', '-c',
                         f'stat -c %s /models/{MODEL.rsplit(".", 1)[0]}.download 2>/dev/null || echo 0'])
            partial = int((r.stdout.strip() or '0').split()[-1]) if r.returncode == 0 else 0
            if partial > 32 * 2**20:
                break
            time.sleep(1)
        killed = self.sh(['docker', 'kill', f'{self.project}-killed-setup']).returncode == 0
        setup.wait(timeout=120)
        process, log = self.start_block(script_up, 'kill-recover')
        healthy = self.wait_healthy(process, log, self.args.restart_limit + 900)
        text = log.read_text(errors='replace')
        ok = (killed and partial > 0 and healthy is not None and f'downloaded {MODEL_BYTES} bytes' in text
              and (self.health() or {}).get('provider') == provider)
        self.record('a setup killed mid-download recovers: the README command re-fetches, verifies and serves', ok, t,
                    partial_bytes_at_kill=partial, killed=killed, seconds_to_healthy=healthy, log='kill-recover.log')
        return process

    def corrupt_asset(self, process, script_up):
        """A corrupted verified asset is refused by hash and not overwritten; moving it aside lets setup recover."""
        t = time.monotonic()
        self.interrupt(process)
        self.compose('down')
        self.in_models(f'printf x | dd of=/models/{CORRUPT_ASSET} bs=1 seek=4096 conv=notrunc 2>/dev/null',
                       'corrupt.log')
        r = self.sh(f'timeout 900 {script_up}', log_name='corrupt-up.log', timeout=920)
        state = self.compose('ps', '-a', '--format', '{{.State}}', 'api').stdout.strip()
        refused = (r.returncode not in (0, 124) and f'Unverified encoder asset: {CORRUPT_ASSET}' in r.stdout
                   and state != 'running')
        still_there = self.in_models(f'test -f /models/{CORRUPT_ASSET}', 'corrupt-kept.log').returncode == 0
        self.in_models(f'mv /models/{CORRUPT_ASSET} /models/{CORRUPT_ASSET}.corrupt', 'corrupt-aside.log')
        recover = self.setup_once('corrupt-recover.log')
        ok = refused and still_there and recover.returncode == 0 and f'Verified {CORRUPT_ASSET}' in recover.stdout
        self.record('a corrupted asset is rejected by hash, never loaded or overwritten; moved aside, setup re-fetches',
                    ok, t, exit=r.returncode, api_state=state or 'not created', refused=refused,
                    left_in_place=still_there, recovered=recover.returncode == 0, log='corrupt-up.log')

    # --- modes ----------------------------------------------------------------------------------------------
    def serve(self, script, provider):
        process = self.first_run(script)
        if not process:
            return
        self.provenance()
        self.disk()
        self.health_and_face(provider)
        self.frontend()
        self.smokes(gpu=provider == 'cuda')
        script_up = script.strip().splitlines()[-1]  # the README's `... docker compose ... up`, run in the checkout
        process = self.down_up(process, script_up, provider)
        self.missing_terms(process, script_up)
        process = self.kill_during_setup(script_up, provider)
        self.corrupt_asset(process, script_up)

    def expect_failure(self, script, name, patterns, limit):
        t = time.monotonic()
        process, log = self.start_block(script, 'quickstart')
        try:
            process.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            self.interrupt(process)
        self.checkout = self.workdir / 'facemorph.me'
        text = log.read_text(errors='replace')
        api_logs = self.compose('logs', '--no-color', 'api', log_name='api.log').stdout
        state = self.compose('ps', '-a', '--format', '{{.State}}', 'api').stdout.strip()
        matched = [p for p in patterns if re.search(p, text + api_logs, re.I)]
        ok = (process.returncode not in (0, None) and len(matched) == len(patterns)
              and '"provider":"cpu"' not in api_logs and SERVING not in text)
        self.record(name, ok, t, exit=process.returncode, matched=matched, api_state=state or 'not created',
                    log='quickstart.log')

    def run(self):
        block = extract_block(self.args.readme, MARKERS[self.args.mode])
        script, changes = substitute(block, self.args.repo, self.args.branch)
        self.result.update(environment=self.environment(), block=block, substitutions=changes,
                           run_kind='development' if changes else 'canonical',
                           isolation={'COMPOSE_PROJECT_NAME': self.project, 'CHECKFACE_PORT': self.port,
                                      'BUILDX_BUILDER': self.builder or 'default (build cache reused)'},
                           workdir=str(self.workdir))
        for change in changes:
            self.log(f'SUBSTITUTED {change}')
        self.log(f'isolation {json.dumps(self.result["isolation"])}; block:\n{script}')
        self.preflight()
        try:
            if self.args.mode == 'gpu':
                self.serve(script, 'cuda')
            elif self.args.mode == 'cpu':
                self.serve(script, 'cpu')
            elif self.args.mode == 'gpu-absent':
                self.expect_failure(script, 'no NVIDIA runtime: the quickstart fails clearly and never serves on CPU',
                                    [r'could not select device driver|nvidia'], self.args.first_run_limit)
            elif self.args.mode == 'gpu-unsupported':
                self.expect_failure(script, 'an unsupported GPU stops at the capability check with a plain message',
                                    [r'has compute capability \d+\.\d+', r'supports compute capability',
                                     r'docker compose -f self-host/compose.yml up'], self.args.first_run_limit)
                self.cpu_on_same_host()
        except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - recorded as a failed run, then cleaned up
            self.record('harness completed', False, time.monotonic(), error=f'{type(error).__name__}: {error}')
        finally:
            self.cleanup()
        self.result['finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.result['passed'] = not self.failed and bool(self.result['assertions'])
        self.save()
        self.log(f'{"PASSED" if self.result["passed"] else "FAILED"}: {self.out / "result.json"}')
        return 0 if self.result['passed'] else 1

    def cpu_on_same_host(self):
        """The explicit CPU override, run from the same checkout after the capability refusal, serves a face."""
        t = time.monotonic()
        self.compose('down')
        cpu = extract_block(self.args.readme, MARKERS['cpu']).strip().splitlines()[-1]
        process, log = self.start_block(cpu, 'cpu-override')
        healthy = self.wait_healthy(process, log, self.args.first_run_limit)
        ok = healthy is not None and (self.health() or {}).get('provider') == 'cpu'
        self.record('the CPU override serves on the same host', ok, t, seconds_to_healthy=healthy, command=cpu,
                    log='cpu-override.log')
        if ok:
            self.health_and_face('cpu')
        self.interrupt(process)
        self.sh(['docker', 'compose', '-f', 'self-host/compose.yml', 'down'])

    def cleanup(self):
        if self.checkout and self.checkout.exists():
            self.compose('logs', '--no-color', log_name='compose-final.log')
            if not self.args.keep:
                for files in ([], ['-f', 'self-host/compose.yml']):
                    self.sh(['docker', 'compose', *files, 'down', '--volumes', '--rmi', 'local', '--remove-orphans'],
                            timeout=600)
        if self.builder and not self.args.keep:
            self.sh(['docker', 'buildx', 'rm', '--force', self.builder])
        if not self.args.keep:
            shutil.rmtree(self.workdir, ignore_errors=True)
        self.log('cleanup: ' + ('kept project, builder and workdir (--keep)' if self.args.keep
                                else 'project volumes, local images, builder and workdir removed'))


def jpeg_size(data):
    """(width, height) from a JPEG's first SOF marker, without an imaging library."""
    if data[:2] != b'\xff\xd8':
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker, length = data[i + 1], struct.unpack('>H', data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack('>HH', data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=sorted(MARKERS), required=True)
    parser.add_argument('--out', required=True, help='evidence directory (result.json and logs)')
    parser.add_argument('--readme', default=str(HERE / 'README.md'))
    parser.add_argument('--repo', help=f'development run: clone this instead of {CANONICAL_REPO}')
    parser.add_argument('--branch', help=f'development run: clone this branch instead of {CANONICAL_BRANCH}')
    parser.add_argument('--port', type=int, help='host port for the API (default: a free one)')
    parser.add_argument('--workdir', help='parent of the empty run directory (default: the system temp directory)')
    parser.add_argument('--chrome', help='Chrome/Chromium binary for the frontend check')
    parser.add_argument('--first-run-limit', type=int, default=5400, help='seconds allowed for the first run')
    parser.add_argument('--restart-limit', type=int, default=600, help='seconds allowed for a restart to be healthy')
    parser.add_argument('--reuse-build-cache', action='store_true',
                        help='use the default builder (a faster rerun; the result then does not prove an empty cache)')
    parser.add_argument('--min-free-gib', type=float, default=35,
                        help='refuse to start below this much free space under the Docker root (CUDA image ~13 GB, '
                             'held twice while a fresh builder loads it, plus ~1.7 GB of models)')
    parser.add_argument('--keep', action='store_true', help='keep the project, builder and workdir for inspection')
    # A cancelled or timed-out CI job sends SIGTERM: turn it into an exception so cleanup still removes the images,
    # volumes and build cache.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('SIGTERM')))
    return Run(parser.parse_args(argv)).run()


if __name__ == '__main__':
    sys.exit(main())
