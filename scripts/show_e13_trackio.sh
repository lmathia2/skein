#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
export UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/skein-uv-cache}
cd "$root"
exec uv run trackio show --project "${TRACKIO_PROJECT:-skein-harbor}" "$@"
