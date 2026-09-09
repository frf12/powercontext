# Guided Config Agent Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the guided configuration wizard cover every installable Agent integration, repeat the remaining-Agent menu until the user explicitly finishes, and enable ordinary Source capture by default without a redundant prompt.

**Architecture:** Add one wizard-facing catalog that derives its seven first-class entries from `FIRST_CLASS_HOSTS` and appends the separately supported WorkBuddy entry. Keep Agent-specific environment and plugin-configuration differences in immutable metadata, while `config_wizard.py` owns the interaction, generated client file, Scope instructions, and installation next steps.

**Tech Stack:** Python 3.11+, Typer, InquirerPy, pytest, Ruff, ty.

---

### Task 1: Add the shared wizard Agent catalog

**Files:**
- Create: `src/powercontext/cli/config_wizard_agents.py`
- Test: `tests/test_config_wizard_agents.py`

- [ ] **Step 1: Write failing catalog tests**

```python
from powercontext.cli.config_wizard_agents import AGENT_SPECS
from powercontext.cli.hosts import FIRST_CLASS_HOSTS


def test_wizard_catalog_covers_first_class_hosts_and_workbuddy() -> None:
    assert tuple(spec.identifier for spec in AGENT_SPECS) == (
        *(host.name for host in FIRST_CLASS_HOSTS),
        "workbuddy",
    )


def test_openclaw_keeps_its_plugin_configuration_contract() -> None:
    spec = next(spec for spec in AGENT_SPECS if spec.identifier == "openclaw")
    assert spec.environment_prefix is None
    assert spec.capture_setting == "autoCapture"
    assert spec.scope_setting == "scopeId"
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_config_wizard_agents.py`

Expected: collection fails because `powercontext.cli.config_wizard_agents` does not exist.

- [ ] **Step 3: Implement immutable Agent metadata**

Create an `AgentSpec` dataclass with `identifier`, bilingual labels, `environment_prefix`, `server_url_name`, `authorization_name`, `capture_setting`, `scope_setting`, and `setup_server_url`. Build `AGENT_SPECS` in the exact order of `FIRST_CLASS_HOSTS`, validating that every first-class host has metadata, then append WorkBuddy. Use these contracts:

```python
_HOST_METADATA = {
    "codex": AgentSpec("codex", "Codex", "Codex", "POWERCONTEXT_CODEX", None, "AUTHORIZATION", "CAPTURE_PROMPTS", "SCOPE_ID", False),
    "claude-code": AgentSpec("claude-code", "Claude Code", "Claude Code", "POWERCONTEXT_CLAUDE", "SERVER_URL", "AUTHORIZATION", "CAPTURE_PROMPTS", "SCOPE_ID", True),
    "dsh": AgentSpec("dsh", "DeepSeek Harness", "DeepSeek Harness", "POWERCONTEXT_DSH", "BASE_URL", "AUTHORIZATION", "CAPTURE_PROMPTS", "SCOPE_ID", False),
    "openclaw": AgentSpec("openclaw", "OpenClaw", "OpenClaw", None, "endpoint", None, "autoCapture", "scopeId", True),
    "opencode": AgentSpec("opencode", "OpenCode", "OpenCode", "POWERCONTEXT_OPENCODE", "BASE_URL", "AUTHORIZATION", "CAPTURE_PROMPTS", "SCOPE_ID", False),
    "pi": AgentSpec("pi", "Pi", "Pi", "POWERCONTEXT_PI", "BASE_URL", "AUTHORIZATION", "CAPTURE_PROMPTS", "SCOPE_ID", False),
    "hermes": AgentSpec("hermes", "Hermes", "Hermes", "POWERCONTEXT_HERMES", "BASE_URL", "AUTHORIZATION", "CAPTURE_TURNS", "SCOPE_ID", False),
}
WORKBUDDY = AgentSpec("workbuddy", "WorkBuddy", "WorkBuddy", "POWERCONTEXT_WORKBUDDY", "SERVER_URL", "AUTHORIZATION", "CAPTURE_PROMPTS", "SCOPE_ID", False)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_config_wizard_agents.py`

Expected: all catalog tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/powercontext/cli/config_wizard_agents.py tests/test_config_wizard_agents.py
git commit -m "feat(config): catalog Agent integration contracts"
```

### Task 2: Repeat the Agent menu and make Source capture implicit

**Files:**
- Modify: `src/powercontext/cli/config_wizard.py`
- Modify: `tests/test_config_wizard.py`

- [ ] **Step 1: Write failing interaction tests**

Add tests that drive `_agents` with Codex, Claude Code, then Finish and assert the menu is shown three times, Codex is absent from the second menu, and no `Capture user prompts as Sources?` prompt occurs. Add an advanced-mode test that answers the capture opt-out and asserts the warning when `topic-memory` is enabled.

```python
def test_agent_menu_repeats_with_configured_agents_removed() -> None:
    state, ui = wizard_state("codex", "default", "claude-code", "default", "none")
    config_wizard._agents(state)
    assert state.agents == ("codex", "claude-code")
    assert ui.choice_ids[1] == ("claude-code", "dsh", "openclaw", "opencode", "pi", "hermes", "workbuddy", "none")
    assert "Capture user prompts as Sources?" not in ui.prompts


def test_advanced_capture_opt_out_warns_for_topic_memory() -> None:
    state, _ = wizard_state("codex", False, "default", "none", advanced=True)
    state.features = {"topic-memory"}
    config_wizard._agents(state)
    assert state.client["POWERCONTEXT_CODEX_CAPTURE_PROMPTS"] == "false"
    assert any("will not drive" in note for note in state.notes)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_config_wizard.py -k 'agent_menu_repeats or capture_opt_out'`

Expected: the old Codex/Claude-only loop stops after its default-No confirmation and always asks the capture question.

- [ ] **Step 3: Implement the remaining-Agent loop**

Import `AGENT_SPECS`, build the choices from the remaining specs, remove the configured entry, and loop without `Configure another Agent?`. Keep `none` as the explicit terminator. Pass the selected `AgentSpec` into `_configure_agent` instead of branching on two string identifiers.

- [ ] **Step 4: Implement capture behavior**

For normal guided mode, set capture to true for every selected integration that supports capture and print one localized explanation. Only when `state.advanced` is true ask whether capture should remain enabled. If the answer is false and `state.features` intersects `{"memory", "topic-memory"}`, add a warning note naming the Agent and explaining that its ordinary conversations cannot drive automatic extraction.

- [ ] **Step 5: Run interaction regression tests**

Run: `.venv/bin/pytest -q tests/test_config_wizard.py`

Expected: all tests pass, including the new loop and capture semantics.

- [ ] **Step 6: Commit**

```bash
git add src/powercontext/cli/config_wizard.py tests/test_config_wizard.py
git commit -m "fix(config): continue Agent configuration explicitly"
```

### Task 3: Generate each Agent's real client contract and next steps

**Files:**
- Modify: `src/powercontext/cli/config_wizard.py`
- Modify: `tests/test_config_wizard.py`

- [ ] **Step 1: Write failing output-contract tests**

Parameterize the environment-based Agents and assert their real URL, authorization, capture, Scope, and context-assembly names. Add a separate OpenClaw test asserting that the client file contains only the generic API token and that next steps contain `openclaw config set` commands for `endpoint`, `autoCapture`, and an explicit `scopeId` when selected.

```python
@pytest.mark.parametrize(
    ("agent", "url_name", "capture_name"),
    [
        ("dsh", "POWERCONTEXT_DSH_BASE_URL", "POWERCONTEXT_DSH_CAPTURE_PROMPTS"),
        ("opencode", "POWERCONTEXT_OPENCODE_BASE_URL", "POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS"),
        ("pi", "POWERCONTEXT_PI_BASE_URL", "POWERCONTEXT_PI_CAPTURE_PROMPTS"),
        ("hermes", "POWERCONTEXT_HERMES_BASE_URL", "POWERCONTEXT_HERMES_CAPTURE_TURNS"),
        ("workbuddy", "POWERCONTEXT_WORKBUDDY_SERVER_URL", "POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS"),
    ],
)
def test_agent_client_fields_follow_real_contract(agent: str, url_name: str, capture_name: str) -> None:
    state = configured_agent_state(agent)
    assert state.client[url_name] == "http://127.0.0.1:8000"
    assert state.client[capture_name] == "true"
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_config_wizard.py -k 'agent_client_fields or openclaw_next_steps'`

Expected: non-Codex/Claude selections or their fields are missing.

- [ ] **Step 3: Generalize environment-based client fields**

Replace the Codex/Claude name branch with `AgentSpec` metadata. Always keep the generic `POWERCONTEXT_CLIENT_SERVER_URL` and token. For environment-based integrations, emit only supported host-specific URL, authorization, capture, Scope, and context-assembly fields; do not create `POWERCONTEXT_CODEX_SERVER_URL` because Codex uses its installed MCP endpoint plus the generic client URL.

- [ ] **Step 4: Generate OpenClaw plugin commands**

Keep OpenClaw's token in `POWERCONTEXT_CLIENT_API_TOKEN`. In next steps, append commands using its actual configuration contract:

```bash
openclaw config set plugins.entries.memory-powercontext.config.endpoint '<server-url>'
openclaw config set plugins.entries.memory-powercontext.config.autoCapture true
openclaw config set plugins.entries.memory-powercontext.config.scopeId '<returned-scope-id>'
```

Do not write invented `POWERCONTEXT_OPENCLAW_*` variables.

- [ ] **Step 5: Generalize planned Scope instructions and setup commands**

Create one planned title per selected Agent. For environment-based Agents, tell the user which real `*_SCOPE_ID` variable receives the returned opaque ID. For OpenClaw, tell the user to run the real `openclaw config set ...scopeId` command. Generate `powercontext setup <agent> --source <checkout>` for all eight entries; add `--server-url` only when `AgentSpec.setup_server_url` is true.

- [ ] **Step 6: Run output and focused wizard tests**

Run: `.venv/bin/pytest -q tests/test_config_wizard.py tests/test_config_wizard_agents.py tests/test_config_cli.py`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/powercontext/cli/config_wizard.py tests/test_config_wizard.py
git commit -m "feat(config): generate all Agent integration settings"
```

### Task 4: Verify the complete user flow

**Files:**
- Modify if needed: `tests/test_config_wizard_ui.py`
- Modify if needed: `docs/superpowers/specs/2026-09-10-guided-config-tui-design.md`

- [ ] **Step 1: Run the complete guided-config regression set**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_config_cli.py \
  tests/test_config_wizard.py \
  tests/test_config_wizard_agents.py \
  tests/test_config_wizard_document.py \
  tests/test_config_wizard_models.py \
  tests/test_config_wizard_network.py \
  tests/test_config_wizard_ui.py
```

Expected: all tests pass.

- [ ] **Step 2: Run code-quality checks**

Run:

```bash
.venv/bin/ruff check src/powercontext/cli/config_wizard*.py tests/test_config_wizard*.py
.venv/bin/ruff format --check src/powercontext/cli/config_wizard*.py tests/test_config_wizard*.py
.venv/bin/ty check src/powercontext/cli/config_wizard*.py
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 3: Perform a real TUI acceptance run**

Run `.venv/bin/powercontext config init --language zh --output <temporary-path>`, select Codex and Claude Code in sequence, verify Codex disappears after configuration, verify no standard capture confirmation appears, select Finish, review the redacted preview, and cancel. Repeat in `--advanced` mode once to verify capture opt-out and the Topic Memory warning. No output file should exist after either cancellation.

- [ ] **Step 4: Commit any acceptance-only corrections**

```bash
git add src/powercontext/cli/config_wizard*.py tests/test_config_wizard*.py docs/superpowers/specs/2026-09-10-guided-config-tui-design.md
git commit -m "test(config): cover guided Agent integration flow"
```
