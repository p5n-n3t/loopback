#!/usr/bin/env bash
# Run actual CLI upgrade/rollback checks; see documented same-version limitation.
set -Eeuo pipefail
exec python3 /src/lab/network/lifecycle.py
