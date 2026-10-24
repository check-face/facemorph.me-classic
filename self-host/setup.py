"""Acquire all verified model assets after explicit licence acknowledgement.

Run by the `setup` service of `docker compose up`. The acknowledgement comes from the
environment: ACCEPT_MODEL_TERMS=yes (or --accept-research-license for explicit
invocations such as CI). Without it this exits non-zero immediately: it never prompts,
never hangs, and never downloads. Re-running is safe - verified files are reused, an
interrupted download is replaced on the next run, and a corrupt file is refused, never
overwritten.
"""
import os
import subprocess
import sys

ACCEPT_FLAG = '--accept-research-license'
TERMS_ENV = 'ACCEPT_MODEL_TERMS'
TERMS_LOCATION = 'self-host/README.md (Model and source terms)'


def acknowledged(argv, environ=None):
    environ = os.environ if environ is None else environ
    if ACCEPT_FLAG in argv:
        return True
    return environ.get(TERMS_ENV, '').strip().lower() == 'yes'


def refusal():
    return (
        f'Model licence terms not acknowledged. Review the terms in {TERMS_LOCATION}, then run: '
        'ACCEPT_MODEL_TERMS=yes docker compose up'
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not acknowledged(argv):
        print(refusal(), file=sys.stderr)
        return 2
    # The prepare scripts gate on the flag themselves; pass it through when acceptance
    # came from the environment so one acknowledgement covers the whole setup.
    forwarded = argv if ACCEPT_FLAG in argv else [*argv, ACCEPT_FLAG]
    for script in ('prepare_model.py', 'prepare_encoder.py'):
        subprocess.run([sys.executable, script, *forwarded], check=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
