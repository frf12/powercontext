# Guided configuration TUI design

## Objective

Make `powercontext config init` use one consistent bilingual terminal UI while retaining the storage-first guided
configuration, generated files, static validation, redaction, backups, and no-deployment boundary introduced by the
guided setup.

The interaction follows the project's existing `InquirerPy` precedent from commit `b6b340ed`. The change is an
interaction-layer replacement, not a rollback of the configuration model.

## User experience

In an interactive terminal:

- short finite choices use an `InquirerPy` selection list with arrow-key navigation and Enter to accept;
- searchable provider lists use `InquirerPy` fuzzy selection, retaining the earlier `(type to search)` behavior;
- yes/no decisions use `InquirerPy` confirmation prompts;
- ordinary values use `InquirerPy` text prompts with visible defaults;
- credentials use `InquirerPy` secret prompts and never display their value or retained default;
- integer inputs use text prompts with the current localized validation and retry messages;
- Ctrl-C or EOF aborts without writing any output file.

Language selection is the first TUI question unless `--language en|zh` is supplied. System detection and unsupported
language fallback remain unchanged. All questions and choices use the selected language.

The existing flow remains storage, deployment scenario, memory capabilities, Dashboard/access, required model
connections, processing schedules, and one-at-a-time Agent configuration. The wizard still previews changes before
the final save.

## Interaction adapter

`WizardUI` remains the only interaction interface used by the configuration and model collectors. It selects a
backend once:

- `InquirerPy` when both stdin and stdout are TTYs;
- the existing Typer text backend for redirected input, tests, automation, and terminals where `InquirerPy` cannot be
  loaded.

This preserves deterministic scripted tests and avoids scattering terminal detection across the business flow.
Callers continue using `choose`, `ask`, `confirm`, and `integer`; no configuration code imports `InquirerPy` directly.

For `choose`, lists with a small number of choices use `select`. Provider-selection callers explicitly request fuzzy
search through a small UI option instead of inferring it from labels or list size. Both backends return the same stable
choice identifiers.

## Validation and file behavior

The TUI does not remove configuration checks. Before saving, the wizard continues to perform syntax and static Server,
Runtime, and Topic Memory provider validation. It does not contact databases, model APIs, an existing Server, or Agent
plugins, and it does not start services, migrate data, create Scope records, or install plugins.

The preview, credential redaction, `0600` atomic writes, backup creation, concurrent-change detection, and generated
next-step document remain independent of the UI backend.

## Failure handling

- A validation failure stays on the relevant prompt when it can be corrected locally.
- A configuration-level validation failure stops before the save and reports only safe field information.
- `KeyboardInterrupt`, EOF, or TUI cancellation exits non-zero and writes nothing.
- If an interactive process cannot import `InquirerPy`, it falls back to the text backend and emits one concise notice;
  it does not fail midway through configuration.

## Compatibility

`powercontext config init` is the product entry point. `--template` retains the earlier non-guided model-free template.
The repository launcher is only an isolated-checkout convenience and contains no wizard behavior.

Existing configuration and model collector tests continue through the text backend. TUI-specific tests exercise the
adapter with patched `InquirerPy` prompt objects and verify prompt type, localized message, defaults, secret handling,
stable return values, cancellation, and fuzzy provider selection. A real pseudo-terminal acceptance run verifies arrow
selection, confirmation, hidden input, final preview, and cancel-without-write behavior.

## Acceptance criteria

1. Every interactive choice in the guided wizard is navigable without typing numeric identifiers.
2. Provider selection retains type-to-search behavior.
3. Every interactive prompt is localized consistently and credentials remain hidden.
4. Redirected input and the existing automated tests continue to work without TUI escape sequences.
5. All generated configuration values and validation results are identical for equivalent TUI and text-backend answers.
6. Cancelling at any point writes no environment, client, backup, or next-step file.
