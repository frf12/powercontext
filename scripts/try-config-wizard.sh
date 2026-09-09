#!/bin/sh
# Run this checkout without changing the globally installed PowerContext.
set -eu
wizard_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$wizard_root/.venv/bin/powercontext" ]; then
    exec "$wizard_root/.venv/bin/powercontext" config init "$@"
fi
exec uv run --project "$wizard_root" --frozen --extra cli --extra server powercontext config init "$@"
