"""Compare a from-source UI build with the prebuilt bundle.

    python3 frontend/compare-bundle.py <built deploy-self-host directory>

webpack names each build's files with a fresh 20-hex compilation hash, so both sides are compared with
that hash replaced. A source build is not bit-identical: the markdown loader also encodes the contact
email address with randomly chosen HTML entities on every build. Prints one JSON line per difference
and a summary; exits 1 when the file lists differ.
"""
import hashlib, json, re, sys, tarfile
from pathlib import Path

HASHED = re.compile(r'\.([0-9a-f]{20})\.')


def normalised(files):
    """{name: bytes} with the build's compilation hash replaced by HASH in names and contents."""
    hashes = {m.group(1) for name in files for m in [HASHED.search(name)] if m}
    if len(hashes) != 1:
        raise SystemExit(f'Expected one webpack build hash, found {sorted(hashes)}')
    h = hashes.pop()
    return {name.replace(h, 'HASH'): data.replace(h.encode(), b'HASH') for name, data in files.items()}


def main(built):
    root = Path(built)
    ours = normalised({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()})
    with tarfile.open(Path(__file__).with_name('deploy-self-host.tar.gz')) as bundle:
        theirs = normalised({m.name.removeprefix('./'): bundle.extractfile(m).read() for m in bundle.getmembers() if m.isfile()})
    differences = [{'file': n, 'difference': 'only in the build'} for n in sorted(set(ours) - set(theirs))]
    differences += [{'file': n, 'difference': 'only in the bundle'} for n in sorted(set(theirs) - set(ours))]
    for name in sorted(set(ours) & set(theirs)):
        if ours[name] != theirs[name]:
            a, b = ours[name], theirs[name]
            at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            differences.append({'file': name, 'difference': 'content', 'bytes': [len(a), len(b)], 'first_difference_at': at,
                                'build': a[max(0, at - 40):at + 40].decode('utf-8', 'replace'),
                                'bundle': b[max(0, at - 40):at + 40].decode('utf-8', 'replace'),
                                'sha256': [hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()]})
    for d in differences:
        print(json.dumps(d))
    same_list = sorted(ours) == sorted(theirs)
    print(json.dumps({'summary': {'files': len(ours), 'same_file_list': same_list,
                                  'content_differences': sum(d['difference'] == 'content' for d in differences)}}))
    return 0 if same_list else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
