#!/usr/bin/env bash
# Build the client first: FastAPI, rather than Vite, serves this output.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
python_bin="${PYTHON_BIN:-$repo/.venv/bin/python}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  python_bin=python3
fi
# Serialize the client build: Playwright starts one webServer per
# dictionary state concurrently, but every server serves the identical
# Vite output. Concurrent builds race on the same dist directory.
(
  flock -w 600 9 || { echo "[e2e-server] build lock timeout" >&2; exit 1; }
  (cd "$repo/frontend" && npm run build)
) 9>"$repo/.e2e-build.lock"
exec "$python_bin" "$here/serve.py" "$@"
