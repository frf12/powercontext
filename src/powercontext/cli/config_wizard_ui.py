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

"""Bilingual terminal prompts for the guided environment-file configuration."""

from __future__ import annotations

import locale
import os
import re
import subprocess
import sys
from collections.abc import Sequence

import typer


def normalize_language(value: str) -> str:
    """Map English and Chinese locale variants to their supported UI language."""
    language = re.split(r"[-_.@]", value.strip().lower(), maxsplit=1)[0]
    return "zh" if language == "zh" else "en"


def _macos_language() -> str | None:
    """Read macOS's first preferred language without changing system settings."""
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    first = result.stdout.strip().strip("()").strip().split(",", maxsplit=1)[0].strip().strip('"')
    return first or None


def detect_language() -> str:
    """Use the first system locale, falling back to English when unsupported.

    Locale precedence is LC_ALL, LC_MESSAGES, then LANG. In particular, an
    explicit unsupported locale (including C/POSIX) must not fall through to a
    different lower-priority language. macOS preferences are consulted only
    when these environment variables are all absent or empty.
    """
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if value := os.environ.get(name, "").strip():
            return normalize_language(value)
    if sys.platform == "darwin" and (value := _macos_language()):
        return normalize_language(value)
    try:
        value = locale.getlocale()[0]
    except (ValueError, locale.Error):
        value = None
    return normalize_language(value or "")


def choose_language(explicit: str | None = None, previous: str | None = None) -> str:
    """Offer a bilingual language menu unless the CLI explicitly selected one."""
    if explicit is not None:
        return normalize_language(explicit)
    default = normalize_language(previous) if previous is not None else detect_language()
    return WizardUI(default).choose(
        "Language / 语言",
        "Language / 语言",
        [("en", "English", "English"), ("zh", "中文", "中文")],
        default=default,
    )


class WizardUI:
    """Small numbered-menu UI that works in terminals and redirected test input."""

    def __init__(self, language: str) -> None:
        self.language = normalize_language(language)

    def text(self, en: str, zh: str) -> str:
        """Select text without mutating the process locale."""
        return zh if self.language == "zh" else en

    def say(self, en: str, zh: str) -> None:
        """Print an ordinary localized line."""
        typer.echo(self.text(en, zh))

    def section(self, en: str, zh: str) -> None:
        """Print a section heading."""
        typer.echo()
        typer.secho(self.text(en, zh), bold=True, fg=typer.colors.CYAN)

    def choose(self, en: str, zh: str, choices: Sequence[tuple[str, str, str]], default: str) -> str:
        """Return a stable choice identifier from a numeric or identifier answer."""
        identifiers = [identifier for identifier, _, _ in choices]
        if not identifiers or default not in identifiers:
            message = "Wizard choice default must identify one of its choices."
            raise ValueError(message)
        self.say(en, zh)
        for number, (_, en_label, zh_label) in enumerate(choices, start=1):
            typer.echo(f"  {number}. {self.text(en_label, zh_label)}")
        default_number = str(identifiers.index(default) + 1)
        while True:
            answer = self.ask("Choose", "请选择", default=default_number)
            for number, identifier in enumerate(identifiers, start=1):
                if answer.casefold() in {str(number), identifier.casefold()}:
                    return identifier
            self.say(
                f"Enter a number from 1 to {len(choices)}, or a choice identifier.",
                f"请输入 1 到 {len(choices)} 的编号，或选项标识。",  # noqa: RUF001
            )

    def ask(self, en: str, zh: str, default: str = "", secret: bool = False, required: bool = False) -> str:
        """Ask for text, preserving an existing secret without displaying it."""
        while True:
            value = typer.prompt(
                self.text(en, zh),
                default=default,
                type=str,
                hide_input=secret,
                show_default=not secret and bool(default),
            )
            if required and not value.strip():
                self.say("This value cannot be empty.", "此项不能为空。")
                continue
            return value if secret else value.strip()

    def confirm(self, en: str, zh: str, default: bool = True) -> bool:
        """Accept English and Chinese confirmations in either UI language."""
        answer_default = self.text("y" if default else "n", "是" if default else "否")
        while True:
            answer = self.ask(en, zh, default=answer_default).casefold()
            if answer in {"y", "yes", "是"}:
                return True
            if answer in {"n", "no", "否"}:
                return False
            self.say("Enter y or n.", "请输入 是/否，或 y/n。")  # noqa: RUF001

    def integer(self, en: str, zh: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
        """Read an integer within an inclusive range, with localized retries."""
        if default < minimum or (maximum is not None and default > maximum):
            message = "Wizard integer default must be within the requested range."
            raise ValueError(message)
        while True:
            answer = self.ask(en, zh, default=str(default))
            try:
                value = int(answer)
            except ValueError:
                self.say("Enter an integer.", "请输入整数。")
                continue
            if value >= minimum and (maximum is None or value <= maximum):
                return value
            if maximum is None:
                self.say(f"Enter an integer of at least {minimum}.", f"请输入不小于 {minimum} 的整数。")
            else:
                self.say(
                    f"Enter an integer between {minimum} and {maximum}.",
                    f"请输入 {minimum} 到 {maximum} 之间的整数。",
                )
