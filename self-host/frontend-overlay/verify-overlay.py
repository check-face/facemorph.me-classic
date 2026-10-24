"""Lightweight source/provenance audit; full Fable/Docker/browser checks are separate."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
# The pinned source: the vendored copy by default, or a Git checkout of the pinned commit.
public = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root.parent / 'frontend' / 'source'
manifest = json.loads((root / 'source.json').read_text())
if (public / '.git').exists():
    revision = subprocess.check_output(['git', '-C', str(public), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == manifest['revision'], (revision, manifest['revision'])
else:
    # The vendored tree is exactly `git archive <revision>`: every recorded file, byte for byte, and no other.
    listing = dict(reversed(line.split('  ', 1)) for line in (root.parent / manifest['vendored_files']).read_text().splitlines())
    present = sorted(p.relative_to(public).as_posix() for p in public.rglob('*') if p.is_file())
    assert present == sorted(listing), ('Vendored source differs from the pinned file list', sorted(set(present) ^ set(listing)))
    for name, expected in listing.items():
        assert hashlib.sha256((public / name).read_bytes()).hexdigest() == expected, ('Vendored file changed', name)
    revision = manifest['revision']
for name, expected in manifest['overlay_files'].items():
    assert hashlib.sha256((root / 'source' / name).read_bytes()).hexdigest() == expected, name
base = json.loads((public / 'package.json').read_text())
updated = json.loads((root / 'source/package.json').read_text())
for key in set(base) | set(updated):
    if key != 'scripts':
        assert base.get(key) == updated.get(key), ('Changed package configuration', key)
assert all(updated['scripts'][key] == value for key, value in base['scripts'].items())
assert updated['scripts']['build:self-host'] == 'node scripts/build-self-host.cjs'
assert not (root / 'source/package-lock.json').exists(), 'Use unchanged public dependency lock.'
config = (root / 'source/src/Config.fs').read_text()
assert 'FACEMORPH_TRIAL' not in config and 'hf.space' not in config
assert 'let apiAddr = if isSelfHost then "" else "https://api.facemorph.me"' in config
assert 'let canonicalBaseUrl = if isSelfHost then selfHostOrigin else "https://facemorph.me"' in config
assert "typeof window !== 'undefined' ? window.location.origin : ''" in config
assert 'let encodeApiAddr = apiAddr + "/api/encodeimage/"' in config
morph = (root / 'source/src/MorphForm.fs').read_text()
assert morph.replace('            if isSelfHost then prop.custom ("loading", "lazy")\n', '') == (public / 'src/MorphForm.fs').read_text()
assert 'if props.IsOpen && isSelfHost then' in (root / 'source/src/BrowseFacesDialog.fs').read_text()
print(json.dumps({'result':'pass', 'public_revision':revision, 'overlay_hashes':len(manifest['overlay_files']),
  'checks':['public pin (Git checkout, or vendored files against the pinned list)', 'all overlay checksums', 'unchanged dependencies/lock', 'hosted defaults retained',
  'same-origin API/canonical source', 'no trial Config', 'only conditional lazy loading in original MorphForm']}, indent=2))
