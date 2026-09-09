# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Observable behavior of the bilingual configuration wizard's terminal UI."""

from __future__ import annotations

from collections.abc import Callable
from subprocess import CompletedProcess

import pytest
import typer
from typer.testing import CliRunner

import powercontext.cli.config_wizard_ui as wizard_ui


def _invoke(action: Callable[[], None], input_text: str = ""):
    app = typer.Typer()
    app.command()(action)
    return CliRunner().invoke(app, [], input=input_text)


@pytest.fixture
def empty_locale(monkeypatch):
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(wizard_ui.locale, "getlocale", lambda *_args: (None, None))
    monkeypatch.setattr(wizard_ui.sys, "platform", "linux")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("zh_CN.UTF-8", "zh"),
        ("zh-TW", "zh"),
        ("ZH_Hant_HK", "zh"),
        ("en_US.UTF-8", "en"),
        ("en-GB", "en"),
        ("fr_FR.UTF-8", "en"),
        ("C", "en"),
        ("POSIX", "en"),
        ("C.UTF-8", "en"),
        ("", "en"),
        ("unknown", "en"),
    ],
)
def test_normalize_supported_languages_and_fallback(value: str, expected: str) -> None:
    assert wizard_ui.normalize_language(value) == expected


@pytest.mark.usefixtures("empty_locale")
@pytest.mark.parametrize("selected", ["fr_FR.UTF-8", "C", "POSIX", "C.UTF-8"])
def test_first_locale_wins_even_when_lower_priority_language_is_supported(monkeypatch, selected: str) -> None:
    monkeypatch.setenv("LC_ALL", selected)
    monkeypatch.setenv("LC_MESSAGES", "zh_TW.UTF-8")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert wizard_ui.detect_language() == "en"


@pytest.mark.usefixtures("empty_locale")
def test_messages_locale_takes_precedence_over_lang(monkeypatch) -> None:
    monkeypatch.setenv("LC_MESSAGES", "zh_TW.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert wizard_ui.detect_language() == "zh"


@pytest.mark.usefixtures("empty_locale")
@pytest.mark.parametrize("value", ["zh_CN.UTF-8", "zh-TW"])
def test_detect_language_uses_lang(monkeypatch, value: str) -> None:
    monkeypatch.setenv("LANG", value)
    assert wizard_ui.detect_language() == "zh"


@pytest.mark.usefixtures("empty_locale")
def test_missing_locale_defaults_to_english() -> None:
    assert wizard_ui.detect_language() == "en"


@pytest.mark.usefixtures("empty_locale")
@pytest.mark.parametrize(
    ("preferences", "expected"),
    [('(\n    "zh-Hans-CN",\n    "en-US"\n)', "zh"), ('(\n    "fr-FR",\n    "zh-Hans"\n)', "en")],
)
def test_macos_uses_first_preferred_language_only_when_locale_is_absent(monkeypatch, preferences, expected) -> None:
    monkeypatch.setattr(wizard_ui.sys, "platform", "darwin")
    monkeypatch.setattr(
        wizard_ui.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess([], 0, stdout=preferences, stderr=""),
    )
    assert wizard_ui.detect_language() == expected
    monkeypatch.setenv("LANG", "C")
    assert wizard_ui.detect_language() == "en"


@pytest.mark.usefixtures("empty_locale")
def test_macos_preference_failure_falls_back_to_english(monkeypatch) -> None:
    monkeypatch.setattr(wizard_ui.sys, "platform", "darwin")

    def unavailable(*_args, **_kwargs):
        raise OSError

    monkeypatch.setattr(wizard_ui.subprocess, "run", unavailable)
    assert wizard_ui.detect_language() == "en"


@pytest.mark.parametrize("selected", ["en", "zh"])
def test_explicit_language_skips_the_menu(selected: str) -> None:
    observed = []

    def action() -> None:
        observed.append(wizard_ui.choose_language(explicit=selected))

    result = _invoke(action)
    assert result.exit_code == 0
    assert result.output == ""
    assert observed == [selected]


@pytest.mark.parametrize(("previous", "expected"), [(None, "zh"), ("en", "en")])
def test_language_menu_is_always_bilingual_and_preserves_preferred_default(monkeypatch, previous, expected) -> None:
    monkeypatch.setattr(wizard_ui, "detect_language", lambda: "zh")
    observed = []

    def action() -> None:
        observed.append(wizard_ui.choose_language(previous=previous))

    result = _invoke(action, "\n")
    assert result.exit_code == 0
    assert "Language / 语言" in result.output
    assert "English" in result.output
    assert "中文" in result.output
    assert observed == [expected]


@pytest.mark.parametrize("entered", ["2", "zh"])
def test_language_menu_supports_numbers_and_stable_identifiers(monkeypatch, entered: str) -> None:
    monkeypatch.setattr(wizard_ui, "detect_language", lambda: "en")
    observed = []

    def action() -> None:
        observed.append(wizard_ui.choose_language())

    result = _invoke(action, f"{entered}\n")
    assert result.exit_code == 0
    assert observed == ["zh"]


def test_chinese_menu_retries_invalid_selection_and_returns_identifier() -> None:
    ui = wizard_ui.WizardUI("zh")
    observed = []

    def action() -> None:
        observed.append(ui.choose("Storage", "存储", [("sqlite", "Local", "本地")], "sqlite"))

    result = _invoke(action, "9\ninvalid\n1\n")
    assert result.exit_code == 0
    assert "本地" in result.output
    assert "请输入" in result.output
    assert "Invalid" not in result.output
    assert observed == ["sqlite"]


@pytest.mark.parametrize(("entered", "expected"), [("", "saved-credential"), ("new-credential", "new-credential")])
def test_secret_input_hides_existing_and_typed_values(entered: str, expected: str) -> None:
    observed = []

    def action() -> None:
        observed.append(wizard_ui.WizardUI("en").ask("API key", "密钥", "saved-credential", secret=True))

    result = _invoke(action, f"{entered}\n")
    assert result.exit_code == 0
    assert "saved-credential" not in result.output
    assert "new-credential" not in result.output
    assert observed == [expected]


def test_required_value_and_integer_retry_in_selected_language() -> None:
    ui = wizard_ui.WizardUI("zh")
    observed = []

    def action() -> None:
        observed.append(ui.ask("Name", "名称", required=True))
        observed.append(ui.integer("Port", "端口", default=8000, minimum=1, maximum=65535))

    result = _invoke(action, "\n  alice  \nabc\n0\n65536\n9000\n")
    assert result.exit_code == 0
    assert "不能为空" in result.output
    assert "整数" in result.output
    assert observed == ["alice", 9000]


@pytest.mark.parametrize(("entered", "expected"), [("是", True), ("否", False), ("y", True), ("n", False), ("", True)])
def test_chinese_confirmation_accepts_local_and_english_answers(entered, expected) -> None:
    observed = []

    def action() -> None:
        observed.append(wizard_ui.WizardUI("zh").confirm("Continue?", "继续？"))  # noqa: RUF001

    result = _invoke(action, f"{entered}\n")
    assert result.exit_code == 0
    assert observed == [expected]
