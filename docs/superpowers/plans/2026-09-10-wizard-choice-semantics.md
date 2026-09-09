# Wizard Choice Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every guided-setup choice state its consequences and produce a distinct, predictable configuration while fixing duplicate Experience recall.

**Architecture:** Keep `WizardUI` and the existing storage-first eight-step flow. Change orchestration and copy in `config_wizard.py`, retain provider-specific collection in `config_wizard_models.py`, and add focused public-behavior tests before each implementation change. Agent detection is a small pure helper over executable names so it can be tested without starting an Agent.

**Tech Stack:** Python 3.11+, Typer, InquirerPy, pytest, Ruff, ty.

---

## File map

- Modify `src/powercontext/cli/config_wizard.py`: scenario, capability, network, schedule, edit-module, Agent, Scope, and context-assembly behavior.
- Modify `src/powercontext/cli/config_wizard_agents.py`: optional executable metadata used only to choose the initial Agent default.
- Modify `src/powercontext/cli/config_wizard_models.py`: clarify shared Generation/Embedding credentials without changing the provider contract.
- Modify `tests/test_config_wizard_network.py`: assert the three remote-access branches and Dashboard consequences.
- Modify `tests/test_config_wizard.py`: assert capability summaries, existing-config choices, edit-module boundaries, Agent defaults, Scope wording, and unique context sections.
- Modify `tests/test_config_wizard_agents.py`: assert executable metadata and detected-Agent selection.
- Modify `tests/test_config_wizard_models.py`: assert the precise shared-connection prompt.

### Task 1: Separate usage scenario and remote-access decisions

**Files:**
- Modify: `src/powercontext/cli/config_wizard.py:265-458`
- Test: `tests/test_config_wizard_network.py`

- [ ] **Step 1: Write failing tests for the two scenarios and three access branches**

Add tests that drive `_scenario()` and `_network()` through the text backend and assert:

```python
def test_scenario_only_asks_local_or_other_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    state = Wizard(WizardUI("en"), {}, {})
    choices: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        state.ui,
        "choose",
        lambda en, zh, items, default: choices.append(tuple(item[0] for item in items)) or "local",
    )
    _scenario(state)
    assert choices == [("local", "remote")]


def test_reverse_proxy_keeps_loopback_listener_and_uses_public_https_url() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")
    result = _run_network(state, "n\nhttps\n8000\nhttps://memory.example.com\n")
    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "HTTP_HOST"] == "127.0.0.1"
    assert state.client[CLIENT + "SERVER_URL"] == "https://memory.example.com"
    assert "Nginx" in result.output or "Caddy" in result.output


def test_custom_access_asks_for_listener_and_client_url() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")
    result = _run_network(state, "n\ncustom\n0.0.0.0\n9000\nhttps://memory.example.com\n")
    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "HTTP_HOST"] == "0.0.0.0"
    assert state.values[SERVER + "HTTP_PORT"] == "9000"
```

Also update the SSH test to assert that its option says the command must be run on the client and that no public URL is requested.

- [ ] **Step 2: Run the focused network tests and verify failure**

Run: `uv run pytest tests/test_config_wizard_network.py -q`

Expected: new scenario and reverse-proxy tests fail because the custom scenario still exists and HTTPS/custom currently share one branch.

- [ ] **Step 3: Implement two scenarios and three distinct access handlers**

Replace the scenario choices with stable IDs `local` and `remote`. Split the remote branch into focused helpers with these contracts:

```python
def _custom_access(state: Wizard, port: int) -> tuple[str, int, str]:
    """Return bind host, port, and client-visible URL."""


def _reverse_proxy_access(state: Wizard, port: int) -> tuple[str, int, str]:
    """Keep a loopback listener and return its public HTTPS URL."""


def _ssh_forwarding_access(state: Wizard, port: int, dashboard: bool) -> tuple[str, int, str]:
    """Keep loopback, record the client tunnel URL, and emit a client-side command."""
```

Use this ordered menu and `custom` default:

```python
[
    ("custom", "Custom listener address and client URL", "自定义监听地址和客户端 URL"),
    ("https", "HTTPS reverse proxy, such as Nginx or Caddy", "使用 HTTPS 反向代理（如 Nginx、Caddy）"),
    ("ssh", "SSH port forwarding (run the generated command on the client)", "SSH 端口转发（需在客户端执行生成的命令）"),
]
```

Remove the unreachable duplicate `return port, False` in `_stored_network_port()`.

- [ ] **Step 4: Run network tests and commit**

Run: `uv run pytest tests/test_config_wizard_network.py -q`

Expected: all tests pass.

Commit:

```bash
git add src/powercontext/cli/config_wizard.py tests/test_config_wizard_network.py
git commit -m "feat(config): clarify remote access choices"
```

### Task 2: Expose capability, Dashboard, model-sharing, and schedule consequences

**Files:**
- Modify: `src/powercontext/cli/config_wizard.py:280-329,378-389,494-549`
- Modify: `src/powercontext/cli/config_wizard_models.py:154-166`
- Test: `tests/test_config_wizard.py`
- Test: `tests/test_config_wizard_models.py`

- [ ] **Step 1: Write failing consequence-copy tests**

Extend the existing full-memory and processing tests to assert:

```python
assert "Memory, Topic Memory, Profile, Experience, Skill" in result.output
assert "Generation and Embedding" in result.output
assert "every 60 seconds" in result.output
assert "daily at 02:00" in result.output
```

Add a model collector test whose recording UI asserts the prompt is exactly equivalent to:

```python
"Reuse the Generation API address and API key for Embedding? The Embedding model is configured separately."
```

Add a Dashboard test asserting the question explains authenticated access and token generation, while a disabled Dashboard still writes `MCP_ENABLED=true`.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/test_config_wizard.py tests/test_config_wizard_models.py tests/test_config_wizard_network.py -q`

Expected: the new copy assertions fail; existing configuration values remain unchanged.

- [ ] **Step 3: Implement concise labels followed by consequence summaries**

Use short preset labels:

```python
(
    ("full", "Full memory capabilities (recommended)", "完整记忆能力（推荐）"),
    ("base", "Basic memory (no additional model API)", "基础记忆（无需额外模型 API）"),
    ("custom", "Choose capabilities individually (advanced)", "自行选择能力（高级）"),
)
```

After `full`, print the complete family summary and model requirements. Change the Dashboard prompt so its authentication effect is visible before confirmation. Change only the shared Embedding prompt text, not its stored fields. Make the recommended schedule choice or immediate summary state the actual 60-second family checks and `0 2 * * *` Profile default in the selected timezone. Preserve the existing explanation separating schedule, Generation timeout, and Worker timeout.

- [ ] **Step 4: Run focused tests and commit**

Run: `uv run pytest tests/test_config_wizard.py tests/test_config_wizard_models.py tests/test_config_wizard_network.py -q`

Expected: all focused tests pass.

Commit:

```bash
git add src/powercontext/cli/config_wizard.py src/powercontext/cli/config_wizard_models.py tests/test_config_wizard.py tests/test_config_wizard_models.py tests/test_config_wizard_network.py
git commit -m "feat(config): explain wizard choice consequences"
```

### Task 3: Make existing-configuration and partial-edit choices truthful

**Files:**
- Modify: `src/powercontext/cli/config_wizard.py:95-130,750-779`
- Test: `tests/test_config_wizard.py`

- [ ] **Step 1: Write failing tests for continuation and module boundaries**

Use `AgentAnswers` or a recording `WizardUI` to assert the continuation labels describe retaining the other settings, editing selected modules, and confirming every setting. Add an edit-mode test that selects `capabilities` and verifies `_models` and `_processing` are not called automatically:

```python
monkeypatch.setattr(config_wizard, "_models", lambda state: calls.append("models"))
monkeypatch.setattr(config_wizard, "_processing", lambda state: calls.append("processing"))
config_wizard._edit_modules(state)
assert calls == []
```

Add a second test where newly enabling `vector` without an Embedding configuration invokes only the Embedding
collector. A third test starts with a configured Generation connection, newly enables Topic Memory, and asserts that
the existing Generation connection is not asked again.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_config_wizard.py -q`

Expected: the copy test and non-overlapping module test fail because capability editing currently always invokes models and processing.

- [ ] **Step 3: Implement outcome-based continuation and dependency-aware edits**

Replace the continuation labels with:

```python
(
    ("reuse", "Keep other existing settings and review", "保留其余已有配置，直接检查并保存"),
    ("edit", "Edit selected modules", "只修改指定模块"),
    ("configure", "Confirm every setting (existing values are defaults)", "逐项重新确认（现有值作为默认）"),
)
```

Rename partial-edit labels to non-overlapping modules. Give `_models` an optional feature subset and derive its collector
flags from that subset:

```python
def _models(state: Wizard, required_features: set[str] | None = None) -> None:
    selected = state.features if required_features is None else required_features
    generation = bool(selected & {"memory", "topic-memory", "experience", "profile", "skill"})
    embedding = "vector" in selected
    rerank = "rerank" in selected
    if generation or embedding or rerank:
        state.patch(collect_models(state.ui, state.values, generation=generation, embedding=embedding, rerank=rerank))
```

Before `_capabilities()`, capture the old feature set. Afterward calculate `new_features = state.features - old_features`
and remove roles already configured from that set: any Generation-backed new family is satisfied when
`INFERENCE_GENERATION_MODEL` exists, and `vector` is satisfied only when model, profile ID, and dimension all exist.
Pass only the remaining new features to `_models`. Do not invoke `_processing`; `_capabilities` already gives newly
enabled automatic families their safe default schedule. Keep explicit `models` and `processing` entries available.

- [ ] **Step 4: Run the focused tests and commit**

Run: `uv run pytest tests/test_config_wizard.py tests/test_config_wizard_models.py -q`

Expected: all tests pass.

Commit:

```bash
git add src/powercontext/cli/config_wizard.py tests/test_config_wizard.py
git commit -m "feat(config): isolate partial edit modules"
```

### Task 4: Improve Agent defaults and Scope outcomes

**Files:**
- Modify: `src/powercontext/cli/config_wizard.py:597-704`
- Modify: `src/powercontext/cli/config_wizard_agents.py`
- Test: `tests/test_config_wizard.py`
- Test: `tests/test_config_wizard_agents.py`

- [ ] **Step 1: Write failing tests for detected defaults, empty finish, and Scope text**

Extend `AgentAnswers` to record the `default` passed to `choose`. Add tests that monkeypatch `shutil.which` and assert:

```python
assert preferred_agent([AGENT_SPEC_BY_ID["claude-code"]], which=lambda name: "/bin/claude") == "claude-code"
assert preferred_agent(list(AGENT_SPECS), which=lambda name: None) == "codex"
```

Assert the first Agent menu default is detected-or-Codex, subsequent menus default to `none`, and choosing Finish with no Agent invokes a confirmation whose text says no Agent connection configuration will be generated. Assert Scope labels contain “plan”, “already have”, and “unbound” semantics, and that new Scope output continues to distinguish title from opaque ID.

- [ ] **Step 2: Run Agent tests and verify failure**

Run: `uv run pytest tests/test_config_wizard.py tests/test_config_wizard_agents.py -q`

Expected: new default, confirmation, and wording tests fail.

- [ ] **Step 3: Add executable metadata and implement the menu behavior**

Add `executables: tuple[str, ...]` to `AgentSpec`, populated with `("codex",)`, `("claude",)`, `("dsh",)`,
`("openclaw",)`, `("opencode",)`, `("pi",)`, and `("hermes",)` for the matching integrations. WorkBuddy uses an
empty tuple because it is not a command-line executable. Add a pure helper:

```python
def preferred_agent(agents: Sequence[AgentSpec], which: Callable[[str], str | None] = shutil.which) -> str:
    for agent in agents:
        if any(which(command) for command in agent.executables):
            return agent.identifier
    return "codex" if any(agent.identifier == "codex" for agent in agents) else agents[0].identifier
```

Use it only for the first menu default. After one Agent is configured, default to Finish. If Finish is chosen before any Agent, ask once whether to continue without an Agent connection. Replace the Scope labels with planned-new, user-owned-existing, and unbound/fallback outcomes; do not change stored Scope IDs or generated creation commands.

- [ ] **Step 4: Run Agent tests and commit**

Run: `uv run pytest tests/test_config_wizard.py tests/test_config_wizard_agents.py -q`

Expected: all tests pass.

Commit:

```bash
git add src/powercontext/cli/config_wizard.py src/powercontext/cli/config_wizard_agents.py tests/test_config_wizard.py tests/test_config_wizard_agents.py
git commit -m "feat(config): guide Agent and Scope selection"
```

### Task 5: Remove duplicate Experience recall and run acceptance verification

**Files:**
- Modify: `src/powercontext/cli/config_wizard.py:720-730`
- Test: `tests/test_config_wizard.py`

- [ ] **Step 1: Strengthen the context-assembly regression test**

Replace the set-only assertion with ordered uniqueness:

```python
families = [section["family"] for section in assembly["sections"]]
assert families == ["memory", "profile", "topic-memory", "experience"]
assert len(families) == len(set(families))
```

- [ ] **Step 2: Run the regression test and verify failure**

Run: `uv run pytest tests/test_config_wizard.py::test_full_memory_configuration_shares_provider_and_adds_profile_recall -q`

Expected: fail because `experience` occurs twice.

- [ ] **Step 3: Remove the duplicate section append**

Keep exactly one Experience section:

```python
if "experience" in state.features:
    sections.append({"family": "experience", "limit": 2})
```

- [ ] **Step 4: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_config_wizard.py tests/test_config_wizard_agents.py tests/test_config_wizard_models.py tests/test_config_wizard_network.py tests/test_config_wizard_ui.py tests/test_config_wizard_document.py -q
uv run ruff check src/powercontext/cli/config_wizard.py src/powercontext/cli/config_wizard_agents.py src/powercontext/cli/config_wizard_models.py tests/test_config_wizard.py tests/test_config_wizard_agents.py tests/test_config_wizard_models.py tests/test_config_wizard_network.py
uv run ruff format --check src/powercontext/cli/config_wizard.py src/powercontext/cli/config_wizard_agents.py src/powercontext/cli/config_wizard_models.py tests/test_config_wizard.py tests/test_config_wizard_agents.py tests/test_config_wizard_models.py tests/test_config_wizard_network.py
uv run ty check
```

Expected: all focused tests, lint, formatting, and type checks pass.

- [ ] **Step 5: Run a real pseudo-terminal acceptance pass**

From the isolated worktree, run `uv run powercontext config init --language zh --output <temporary-directory>/server.env` and verify:

- the scenario menu has two choices;
- remote access is ordered custom, reverse proxy, SSH;
- Dashboard, full-memory, schedule, and Scope consequences appear before saving;
- Codex or an installed Agent is initially selected, configured Agents disappear, and Finish becomes the default;
- generated Server/client URLs and tunnel instructions match the selected access method;
- cancel at the save prompt creates no file.

- [ ] **Step 6: Commit the regression fix**

```bash
git add src/powercontext/cli/config_wizard.py tests/test_config_wizard.py
git commit -m "fix(config): avoid duplicate Experience recall"
```
