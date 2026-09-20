#!/usr/bin/env bash
# Run backend tests against a disposable SQLite database and storage directory.
# Usage: scripts/test.sh [pytest args...]   (defaults to tests/api tests/util)
set -euo pipefail

cd "$(dirname "$0")/.."

test_dir=$(mktemp -d /tmp/kitchenowl-pantry-tests.XXXXXX)
trap 'rm -rf "$test_dir"' EXIT

if [ "$#" -eq 0 ]; then
  set -- tests/api tests/util
fi

env -u DB_USER -u DB_PASSWORD -u DB_PASSWORD_FILE -u DB_USER_FILE \
  -u DB_HOST -u DB_PORT \
  STORAGE_PATH="$test_dir" DB_DRIVER=sqlite \
  DB_NAME="$test_dir/test.db" \
  .venv/bin/python -m pytest "$@"
