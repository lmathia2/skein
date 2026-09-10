#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
command -v uv >/dev/null 2>&1 || { echo 'error: uv is required' >&2; exit 1; }
uv sync --project "$project_root" --all-groups --extra eval
command -v pier >/dev/null 2>&1 || {
  echo 'error: Pier 0.3.1 is required: uv tool install datacurve-pier==0.3.1' >&2
  exit 1
}
printf 'Skein Harbor/Pier environment ready.\n'
