#!/usr/bin/env python3
"""Fail on any base image that is not pinned by digest.

Every `FROM` in a Dockerfile and every `image:` in a Compose file must name `<ref>@sha256:<64 hex>`.
A `FROM` that names an earlier build stage, or `scratch`, is not an image pull. An image reference
built from a variable (`FROM node:${VERSION}`) floats with that variable and fails too.

    python3 scripts/check-pins.py [path ...]    (default: self-host)

Prints one JSON line per finding and a summary line; exits 1 on any finding.
"""
import json
import re
import sys
from pathlib import Path

DIGEST = re.compile(r'@sha256:[0-9a-f]{64}$')
FROM = re.compile(r'^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?', re.IGNORECASE)
IMAGE = re.compile(r'^\s*image:\s*["\']?([^"\'\s#]+)')


def dockerfile_findings(path, text):
    stages, out = set(), []
    for number, line in enumerate(text.splitlines(), 1):
        match = FROM.match(line)
        if not match:
            continue
        ref, stage = match.group(1), match.group(2)
        if ref.lower() != 'scratch' and ref.lower() not in stages and ('$' in ref or not DIGEST.search(ref)):
            out.append({'file': str(path), 'line': number, 'image': ref, 'problem': 'FROM is not pinned by digest'})
        if stage:
            stages.add(stage.lower())
    return out


def compose_findings(path, text):
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        match = IMAGE.match(line)
        if match and ('$' in match.group(1) or not DIGEST.search(match.group(1))):
            out.append({'file': str(path), 'line': number, 'image': match.group(1), 'problem': 'image is not pinned by digest'})
    return out


def scanned(root):
    root = Path(root)
    files = [root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file())
    for path in files:
        if path.name.startswith('Dockerfile') or path.suffix == '.dockerfile':
            yield path, dockerfile_findings
        elif re.fullmatch(r'(docker-)?compose(\.[\w-]+)?\.ya?ml', path.name):
            yield path, compose_findings


def main(argv):
    findings, files = [], 0
    for root in argv or ['self-host']:
        for path, check in scanned(root):
            files += 1
            findings += check(path, path.read_text())
    for finding in findings:
        print(json.dumps(finding))
    print(json.dumps({'summary': {'files': files, 'findings': len(findings)}}))
    return 1 if findings or not files else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
