# Pinned dependency and Actions sources

References were read on 2026-10-08.

- xlrd 2.0.1 wheel metadata and SHA-256: https://pypi.org/pypi/xlrd/2.0.1/json
  - `xlrd-2.0.1-py2.py3-none-any.whl`
  - `6a33ee89877bd9abc1158129f6e94be74e2679636b8a205b43b85206c3f0bbdd`
  - No transitive runtime dependency; install with `--require-hashes --only-binary=:all:`
- actions/checkout v4.2.2: https://github.com/actions/checkout/commit/11bd71901bbe5b1630ceea73d27597364c9af683
- actions/setup-python v5.6.0: https://github.com/actions/setup-python/commit/a26af69be951a213d495a4c3e4e4022e16d87065
- actions/upload-artifact v4.6.2: https://github.com/actions/upload-artifact/commit/ea165f8d65b6e75b540449e92b4886f43607fa02
- Local runtime, Python 3.12.14: https://www.python.org/downloads/release/python-31214/

These are deliberate verified version pins, not claims that every pinned component is the latest release. The Python workflow follows the 3.12 patch line to receive security updates. Review pins and rerun tests when updating dependencies or Actions. GitHub-hosted workflow execution has not been validated yet.
