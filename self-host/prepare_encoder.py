"""Acquire original public encoder assets only after license acknowledgement."""
import argparse
import bz2
import hashlib
import json
import re
from pathlib import Path
import tempfile
import urllib.request

from encoder_assets import ASSETS, verified_asset

def received(total, content_type, head):
    """What the host sent instead, when it is a small page rather than the model (a host's quota or error page)."""
    if total >= 65536 or not head.lstrip().startswith(b'<'):
        return ''
    title = re.search(rb'<title>([^<]{1,120})</title>', head, re.I)
    page = f': "{title.group(1).decode(errors="replace").strip()}"' if title else ''
    return (f' (the host sent a {total}-byte {content_type} page{page}, not the model; it may be rate-limiting this '
            f'file, so retry later)')


def download_entry(entry, name, directory):
    """Download and verify one asset described by `entry` (url/sha256/bytes[/compression]).

    Reuses a verified existing file, refuses an existing mismatch (never overwrites),
    streams to a .download temporary and replaces the target atomically. Returns the
    number of bytes downloaded (0 when reused).
    """
    target = directory / name
    if target.exists():
        if target.stat().st_size != entry['bytes'] or hashlib.file_digest(target.open('rb'), 'sha256').hexdigest() != entry['sha256']:
            raise ValueError(f'Unverified encoder asset: {name}; move it aside and retry. It was not overwritten.')
        return 0
    downloaded = 0
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=name+'.', suffix='.download', dir=directory, delete=False) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            total = 0
            head = b''
            with urllib.request.urlopen(entry['url'], timeout=120) as response:
                content_type = response.headers.get_content_type()
                source = bz2.BZ2File(response) if entry.get('compression') == 'bz2' and content_type != 'text/html' else response
                while chunk := source.read(1024 * 1024):
                    head = head or chunk[:4096]
                    total += len(chunk)
                    if total > entry['bytes']:
                        raise ValueError(f'Official download exceeds expected size: {name}')
                    digest.update(chunk)
                    output.write(chunk)
            if total != entry['bytes'] or digest.hexdigest() != entry['sha256']:
                raise ValueError(f'Official download did not match deployed checksum: {name}' + received(total, content_type, head))
        temporary.chmod(0o644)
        temporary.replace(target)
        downloaded = total
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return downloaded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accept-research-license', action='store_true', help='Acknowledge permitted use under e4e/FFHQ and dlib iBUG model terms; no general commercial-use grant')
    parser.add_argument('--directory', type=Path, default=Path('/models'))
    args = parser.parse_args()
    if not args.accept_research_license:
        parser.error('Read the e4e/FFHQ and dlib/iBUG model terms, then pass --accept-research-license for permitted use')
    args.directory.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        downloaded = download_entry(ASSETS[name], name, args.directory)
        print(f'downloaded {downloaded} bytes' if downloaded else f'reused verified {name}', flush=True)
        print(f'Verified {name}: {ASSETS[name]["sha256"]}', flush=True)
    (args.directory/'encoder-provenance.json').write_text(json.dumps({
        'assets': ASSETS,
        'acknowledgement': 'Operator acknowledged model terms for permitted research use; no redistribution or commercial grant implied.',
        'license_sources': ['https://github.com/omertov/encoder4editing/blob/main/LICENSE', 'https://github.com/NVlabs/ffhq-dataset#licenses', 'https://dlib.net/face_landmark_detection.py.html'],
    }, indent=2)+'\n')

if __name__ == '__main__':
    main()
