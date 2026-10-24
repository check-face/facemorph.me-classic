# Original UI: prebuilt bundle and vendored source

The default image serves `deploy-self-host.tar.gz`: the original Facemorph UI built from commit
`0abb215f27b16e17e3919cf78b616b6ae4998a5d` plus the [overlay](../frontend-overlay/README.md). The
Dockerfile's `frontend-bundle` stage rejects it unless it matches `deploy-self-host.tar.gz.sha256`,
so the default build needs no .NET, Fable, npm or network clone. It is committed here because it is
small: 31 files, 4.2 MB unpacked, 1.0 MB as the tarball (measured 24 October 2026).
`deploy-self-host.files.sha256` lists every file in it.

`source/` is that commit vendored byte for byte (`git archive 0abb215`), with its npm lock, Fable
tool pin (`.config/dotnet-tools.json`) and .NET SDK pin. To rebuild the UI from source instead, with
the digest-pinned end-of-life .NET 5 SDK and Node 22 inside the build only:

```sh
docker build --target runtime-from-source .
```

To compare a source build with the bundle:

```sh
docker build --target frontend-source -t facemorph-frontend-source .
id=$(docker create facemorph-frontend-source) && docker cp "$id:/src/deploy-self-host" /tmp/ui && docker rm "$id"
python3 frontend/compare-bundle.py /tmp/ui/deploy-self-host
```

Source builds are not bit-identical. Measured 24 October 2026 on two uncached builds from this vendored
source and one from the old build-time clone of `0abb215`: all three have the bundle's 31-file list.
Their differences are webpack's per-build compilation hash, in 9 file names and in references to
them, and `app.<hash>.js` with its source map, where the markdown loader encodes the contact email
address with randomly chosen HTML entities. No other byte differs.

The tarball is made deterministically from that directory:
`tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner --format=gnu -C <dir> -cf - . | gzip -9n`.
