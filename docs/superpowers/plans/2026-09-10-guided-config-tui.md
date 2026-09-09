# Guided Configuration TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the guided wizard's interactive numbered prompts with a consistent bilingual InquirerPy TUI while preserving its deterministic non-TTY backend and configuration behavior.

**Architecture:** `WizardUI` remains the only UI boundary. It selects InquirerPy once when stdin/stdout are TTYs, otherwise retains the existing Typer implementation. A separate `search` method marks long searchable lists without adding UI-library knowledge to model or configuration logic.

**Tech Stack:** Python 3.11+, InquirerPy 0.3, Typer, pytest, Ruff, ty.

---

### Task 1: Add the interactive InquirerPy adapter

**Files:**
- Modify: `src/powercontext/cli/config_wizard_ui.py`
- Modify: `tests/test_config_wizard_ui.py`

- [ ] **Step 1: Write failing backend-selection and choice tests**

Add a fake `InquirerPy.inquirer` object and verify that `WizardUI("zh", interactive=True)` calls `select` with localized
labels, stable values, and the requested default. Verify `search` calls `fuzzy` with `(type to search)` / `（输入可搜索）`.

```python
def test_interactive_choice_uses_localized_select(fake_inquirer):
    ui = WizardUI("zh", interactive=True)
    assert ui.choose("Storage", "存储", [("sqlite", "Local", "本地")], "sqlite") == "sqlite"
    assert fake_inquirer.calls == [("select", "存储", [{"name": "本地", "value": "sqlite"}], "sqlite")]

def test_interactive_search_uses_fuzzy(fake_inquirer):
    ui = WizardUI("en", interactive=True)
    assert ui.search("Provider", "服务商", [("openai", "OpenAI", "OpenAI")], "openai") == "openai"
    assert fake_inquirer.calls[0][0] == "fuzzy"
```

- [ ] **Step 2: Run the choice tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_config_wizard_ui.py -k 'interactive_choice or interactive_search' -q`

Expected: FAIL because `interactive` and `search` do not exist and `choose` still prints numbered text.

- [ ] **Step 3: Implement backend selection, select, and fuzzy search**

Add an optional constructor override for tests and a lazy loader. Do not import InquirerPy from configuration modules.

```python
class WizardUI:
    def __init__(self, language: str, *, interactive: bool | None = None) -> None:
        self.language = normalize_language(language)
        self.interactive = (
            sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        )

    def choose(self, en, zh, choices, default):
        if self.interactive:
            return str(inquirer.select(
                message=self.text(en, zh),
                choices=[{"name": self.text(a, b), "value": value} for value, a, b in choices],
                default=default,
            ).execute())
        return self._text_choose(en, zh, choices, default)

    def search(self, en, zh, choices, default):
        if self.interactive:
            return str(inquirer.fuzzy(
                message=self.text(en, zh),
                choices=[{"name": self.text(a, b), "value": value} for value, a, b in choices],
                default=default,
                instruction=self.text("(type to search)", "（输入可搜索）"),
            ).execute())
        return self._text_choose(en, zh, choices, default)
```

If importing InquirerPy fails, switch this instance to the text backend, print one localized notice, and retry through
`_text_choose`.

- [ ] **Step 4: Run the choice tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_config_wizard_ui.py -k 'interactive_choice or interactive_search' -q`

Expected: PASS.

- [ ] **Step 5: Commit the choice adapter**

```bash
git add src/powercontext/cli/config_wizard_ui.py tests/test_config_wizard_ui.py
git commit -m "feat(config): add InquirerPy wizard choices"
```

### Task 2: Move text, secret, confirmation, and integer prompts to the TUI backend

**Files:**
- Modify: `src/powercontext/cli/config_wizard_ui.py`
- Modify: `tests/test_config_wizard_ui.py`

- [ ] **Step 1: Write failing prompt-type tests**

Verify `ask` uses `text`, secret input uses `secret` without exposing its retained default, `confirm` uses `confirm`, and
integer validation repeats through `text` with the selected language.

```python
def test_interactive_secret_preserves_empty_retained_value(fake_inquirer):
    fake_inquirer.answers = [""]
    assert WizardUI("en", interactive=True).ask("API key", "密钥", "saved", secret=True) == "saved"
    assert "saved" not in repr(fake_inquirer.calls)

def test_interactive_confirm_uses_native_boolean(fake_inquirer):
    fake_inquirer.answers = [False]
    assert WizardUI("zh", interactive=True).confirm("Continue?", "继续？") is False
```

- [ ] **Step 2: Run the prompt tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_config_wizard_ui.py -k 'interactive_secret or interactive_confirm or interactive_text or interactive_integer' -q`

Expected: FAIL because these methods still call Typer.

- [ ] **Step 3: Implement TUI prompt dispatch**

Use `inquirer.text`, `inquirer.secret`, and `inquirer.confirm`. Keep required/integer retry loops in `WizardUI` so both
backends have identical rules. A secret prompt gets no retained value as its displayed default; an empty answer returns
the prior value in memory.

- [ ] **Step 4: Verify prompt tests and the existing text backend**

Run: `.venv/bin/python -m pytest tests/test_config_wizard_ui.py -q`

Expected: all UI tests PASS, including redirected-input tests with no terminal escape sequences.

- [ ] **Step 5: Commit TUI prompts**

```bash
git add src/powercontext/cli/config_wizard_ui.py tests/test_config_wizard_ui.py
git commit -m "feat(config): use TUI prompts throughout wizard"
```

### Task 3: Mark Provider selection searchable and verify the complete wizard

**Files:**
- Modify: `src/powercontext/cli/config_wizard_models.py`
- Modify: `tests/test_config_wizard_models.py`
- Modify: `tests/test_config_wizard.py`

- [ ] **Step 1: Write the failing Provider search test**

Extend the model-test UI double with `search`, record that call, and assert Provider selection uses it while protocol,
schedule, and Agent lists continue using ordinary selection.

```python
def test_provider_selection_uses_searchable_menu():
    ui = Answers("bailian", "", "qwen-plus", "key")
    collect_models(ui, {}, generation=True, embedding=False)
    assert "Generation service" in ui.search_prompts
    assert "Generation service" not in ui.choose_prompts
```

- [ ] **Step 2: Run the Provider test and verify RED**

Run: `.venv/bin/python -m pytest tests/test_config_wizard_models.py -k provider_selection_uses_searchable_menu -q`

Expected: FAIL because `_new_connection` calls `choose`.

- [ ] **Step 3: Use the searchable UI entry point**

Change only `_new_connection` Provider selection from `ui.choose(...)` to `ui.search(...)`. Keep the custom API protocol
as a short ordinary selection.

- [ ] **Step 4: Run all focused regression checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_config_cli.py tests/test_config_wizard*.py tests/test_env_file.py -q
.venv/bin/ruff check src/powercontext/cli/config.py src/powercontext/cli/config_wizard*.py tests/test_config_cli.py tests/test_config_wizard*.py
.venv/bin/ruff format --check src/powercontext/cli/config.py src/powercontext/cli/config_wizard*.py tests/test_config_cli.py tests/test_config_wizard*.py
.venv/bin/ty check src/powercontext/cli/config.py src/powercontext/cli/config_wizard*.py
```

Expected: all tests and checks PASS.

- [ ] **Step 5: Run real pseudo-terminal acceptance**

Run the isolated checkout's `powercontext config init --language zh --output <temporary-path>`. Verify arrow-key selection,
fuzzy Provider search, native confirmation, hidden credential input, localized preview, successful static validation,
and Ctrl-C cancellation with no files.

- [ ] **Step 6: Commit the completed TUI**

```bash
git add src/powercontext/cli/config_wizard_models.py tests/test_config_wizard_models.py tests/test_config_wizard.py
git commit -m "feat(config): make Provider selection searchable"
```
