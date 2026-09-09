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
- ordinary values show the default in the prompt but keep the input buffer empty, so Enter accepts the default and
  typing replaces it rather than appending to it;
- credentials use `InquirerPy` secret prompts and never display their value or retained default;
- integer inputs use text prompts with the current localized validation and retry messages;
- Ctrl-C or EOF aborts without writing any output file.

Language selection is the first TUI question unless `--language en|zh` is supplied. System detection and unsupported
language fallback remain unchanged. All questions and choices use the selected language.

The existing flow remains storage, deployment scenario, memory capabilities, Dashboard/access, required model
connections, processing schedules, and one-at-a-time Agent configuration. The wizard still previews changes before
the final save. Choice labels describe their user-visible result; details that would make a menu item too long appear
as an immediate consequence summary after selection.

## Decision flow and choice semantics

Storage stays first. Its choices distinguish creating or reusing local storage, connecting OceanBase, and configuring
only a client for an existing PowerContext Server. When an existing data location or environment file is detected, the
wizard reports what was found before asking whether to retain the remaining model, processing, and network settings.
The continuation choices are phrased as outcomes: retain the other existing settings and proceed to review, edit
selected modules, or confirm every setting again with existing values as defaults.

The usage-scenario step has two choices: use PowerContext only on the current machine, or use the Server from another
machine. It does not offer a separate "custom deployment" scenario because listener and access decisions belong to the
network step.

The memory capability presets are:

- full memory capabilities, which enables Memory, Topic Memory, Profile, Experience, Skill, and semantic retrieval;
- basic memory, which supports explicit Agent saves and full-text recall without additional model APIs;
- an advanced custom capability selection.

After the full preset is selected, the wizard displays the complete enabled-family summary and states that Generation
and Embedding model APIs are required. The custom path keeps individual capability decisions explicit. It does not
silently enable a family that was not selected.

Enabling the Dashboard explicitly states that it also enables authenticated access and creates or retains a private
Server token. Disabling the Dashboard does not disable the MCP endpoint. For local-only use, the wizard retains a safe
loopback default but lets an existing non-default valid port remain editable.

Remote access offers these choices in order:

1. Custom listener address and client URL.
2. HTTPS reverse proxy, such as Nginx or Caddy.
3. SSH port forwarding, with a command that the user must run on the client machine.

Each choice has distinct behavior. Custom access asks for the bind address, Server port, and complete client-visible
URL. Reverse-proxy access keeps the PowerContext listener on loopback by default and asks primarily for the public
HTTPS URL; it states that certificate and proxy installation are outside the wizard. SSH forwarding keeps the Server
on loopback, asks for the SSH host and client-side port, generates the exact tunnel command, and never implies that the
wizard starts or maintains the tunnel. A bare scheme such as `https://` is never presented as an acceptable URL.

When model connections are shared, the wizard says precisely what is shared. Embedding may reuse the Generation API
address and API key while still requiring its own model name, dimension, and profile. Reranking either reuses the
Generation model or asks for a separate Reranking model. Recommended processing schedules expose their concrete
defaults in the choice label or immediate summary; the wizard continues to distinguish polling intervals from model
and Worker timeouts.

Partial editing uses non-overlapping modules: memory capabilities, model connections, background processing schedules,
Dashboard and access, Agent connections, and advanced limits/logging. If a capability change introduces a missing
dependency, the wizard opens only the newly required model or schedule questions instead of making the user repeat all
previous answers.

## Agent integration configuration

The Agent step presents every installable Agent integration, not every directory under `integrations/`. Its catalog is
Codex, Claude Code, DeepSeek Harness, OpenClaw, OpenCode, Pi, Hermes, and WorkBuddy. LangChain, LangGraph, Pydantic AI,
OpenDAL, and other library or infrastructure integrations are outside this step because they do not share the Agent
plugin installation contract.

The wizard must reuse a shared catalog or shared metadata derived from the existing setup commands instead of keeping
an independent Codex/Claude-only list. Each catalog entry describes its display label, setup command, environment
prefix, Source-capture setting, Server URL/authentication mechanism, and Scope mechanism. Agent-specific differences
remain explicit: for example, OpenClaw stores an explicit Scope in plugin configuration rather than pretending that it
supports another host's environment variable.

Configuration is one Agent at a time. After one Agent is configured, the wizard returns directly to the menu with that
Agent removed. It asks no intermediate "configure another" confirmation. The user advances only by choosing the
explicit Finish item or after configuring the last available Agent.

On the first visit, Finish is not the default: the wizard prefers a detected Agent and otherwise Codex. After at least
one integration has been configured, Finish becomes the default. Finishing with no Agent configuration requires a
confirmation that no Agent connection file will be generated.

For the standard guided flow, selecting an Agent enables that integration's ordinary prompt or turn capture whenever
the integration supports it. The wizard explains that capture creates Source evidence and does not capture unsupported
content such as Codex or Claude final replies. It does not ask a redundant yes/no question. In advanced mode, the user
may disable capture; when automatic Memory or Topic Memory is enabled, the preview warns that ordinary conversations
from that Agent will not drive those processors. Explicit Memory writes and workflow-specific Sources such as Work
Contracts and Handoff boundaries remain available and are not misrepresented as substitutes for ordinary prompt
capture.

The generated client environment and next-steps document use the selected integration's real configuration contract.
The wizard continues to generate files and commands only; it does not install plugins or start an Agent.

Scope choices describe when work happens:

- plan a new isolated Scope, which is created after saving by following the generated instructions;
- bind a Scope ID the user already has;
- leave the Agent unpinned and use session/workspace binding followed by the Server default.

Planning a Scope generates a readable Agent-prefixed title, but never presents that title as the real Scope ID. The
Server-created opaque ID must be written back to the matching Agent configuration.

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
7. All eight installable Agent integrations are selectable, and a configured Agent disappears before the menu repeats.
8. Standard full-memory setup enables supported Source capture without asking; advanced opt-out produces a clear
   automatic-memory coverage warning.
9. Generated fields and installation steps follow each integration's actual contract instead of synthesizing
   Codex/Claude-shaped variables for other hosts.
10. Every remote-access choice produces a distinct listener/client configuration and accurately states the external
    work still required.
11. Dashboard, full-memory, shared-model, recommended-schedule, and Scope choices display their important consequences
    before the user leaves the relevant step.
12. Partial editing does not make one module unexpectedly re-run unrelated configuration modules.
13. The generated context-assembly configuration contains each requested family once, and network-port validation has
    no unreachable duplicate branch.
