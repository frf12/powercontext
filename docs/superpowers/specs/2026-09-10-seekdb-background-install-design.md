# seekDB Background Dependency Installation Design

## Goal

When a user selects embedded seekDB in `powercontext config init`, start installing the current PowerContext release's
seekDB dependency in the background while the user completes the remaining wizard. If installation is still running
after configuration files are saved, wait in the same terminal with an activity indicator. Do not require a separate
status command.

## Scope

This version only supports incremental dependency installation. It does not reinstall PowerContext, reconstruct the
original `uv tool install` source, or offer the documented full-install command as an automatic recovery path.

The wizard continues to generate configuration files and does not start Server, create a database, migrate data,
create Scopes, or install Agent integrations. A user must explicitly approve the seekDB dependency installation because
it changes the Python environment and may download approximately 110–130 MB.

## Dependency source of truth

The installer reads the requirements declared by the currently installed `powercontext` distribution. It evaluates
the requirement markers with `extra=seekdb` for the current platform and uses those requirement strings as the
resolver input. It does not duplicate the `pylibseekdb` version range in wizard source code.

The installer must exclude a self-referential `powercontext[...]` requirement if one is present. Before starting, it
checks that the non-seekDB requirements selected by the extra are already satisfied. The supported incremental path
may add the missing seekDB distribution and its own dependencies, but it must not replace the running PowerContext
distribution.

If distribution metadata is unavailable, the extra is not declared, the current platform is unsupported, `uv` is
unavailable, or the current Python environment is not writable, the wizard does not start a background task and
prints a concise reason.

## Download source selection

The background worker uses the current `uv` and process configuration first. Existing `UV_DEFAULT_INDEX`,
`UV_INDEX_URL`, project configuration, and standard proxy variables remain authoritative.

Without an explicit index override, the worker first performs a `uv pip install --dry-run` using the normal source. If
resolution fails, it retries the dry run through `https://mirrors.aliyun.com/pypi/simple/`. The actual install uses the
first source whose dry run succeeds. If an installation using the normal source fails, the worker may retry once with
the Aliyun source. No HTTP mirror or insecure-host exception is used.

## Background task

After the seekDB path is collected and the dependency is missing, the wizard explains the download size and asks:

> Install the seekDB dependency in the background while you continue? [Yes]

On confirmation it starts one supervised background worker. The worker runs `uv pip install` against `sys.executable`,
captures output away from the prompt UI, and publishes its current phase and final result through a task object. Only
one task may be active in one wizard.

The user continues through scenario, capabilities, access, models, schedules, and Agent configuration. The background
task does not print progress while an interactive prompt owns the terminal.

If the wizard is cancelled before the final wait, it requests termination of an unfinished installer and does not
start another operation. Already completed dependency installation is not rolled back.

## Completion experience

After the user confirms and the configuration files are saved, the wizard checks the task:

- If installation and import validation already succeeded, print the installed `pylibseekdb` version.
- If still running, show an indeterminate activity bar with the current phase and elapsed time, then wait in the same
  terminal. The UI must not invent a byte percentage because `uv` does not expose a stable byte-progress API.
- If installation fails, print a concise sanitized reason and one copyable incremental `uv pip install` command using
  the current Python interpreter and the requirements read from release metadata. Use the Aliyun HTTPS index in the
  command when normal-source network resolution failed and the mirror was the viable fallback.
- If the platform has no compatible wheel, do not print a command that cannot succeed; recommend SQLite or OceanBase.

The generated `.env` remains valid configuration after an installation failure, but the finish page states that Server
must not be started with seekDB until the dependency is installed.

## Validation

Tests use fake process runners and clocks; they never access package indexes or mutate the test Python environment.
Coverage includes metadata-derived requirements, installed/no-install behavior, background overlap, final waiting,
normal-source to Aliyun fallback, sanitized failures, manual command rendering, unsupported platforms, cancellation,
and the existing no-terminal-escape behavior for redirected input.
