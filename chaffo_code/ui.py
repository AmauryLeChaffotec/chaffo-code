from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Any


class Console:
    """Petit helper d'affichage pour garder le CLI agreable.

    On reste sans dependance externe. Les couleurs utilisent simplement les
    codes ANSI supportes par les terminaux modernes.
    """

    coral = "\033[38;5;209m"
    cyan = "\033[38;5;117m"
    muted = "\033[38;5;244m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    def __init__(self, use_color: bool = True) -> None:
        self.use_color = use_color and "NO_COLOR" not in os.environ

    def color(self, value: str, code: str) -> str:
        if not self.use_color:
            return value
        return f"{code}{value}{self.reset}"

    def banner(self, model: str, workspace: Path) -> None:
        title = self.color("CHAFFO CODE", self.coral + self.bold)
        subtitle = self.color("local coding agent", self.muted)

        print()
        print(self.color("+--------------------------------------------------+", self.coral))
        print(self.color("| * Welcome to Chaffo code                         |", self.coral))
        print(self.color("+--------------------------------------------------+", self.coral))
        print(f"  {title}  {subtitle}")
        print(f"  {self.color('model', self.muted)}     {model}")
        print(f"  {self.color('workspace', self.muted)} {workspace}")
        print()

    def section(self, title: str) -> None:
        print()
        print(self.color(f"> {title}", self.coral + self.bold))

    def plan(self, tasks: list[str]) -> None:
        self.section("Plan")
        for index, task in enumerate(tasks, start=1):
            print(f"  {self.color(str(index) + '.', self.cyan)} {task}")

    def user_prompt(self, prompt: str) -> None:
        self.section("Demande recue")
        line_count = len(prompt.splitlines()) or 1
        char_count = len(prompt)
        print(f"  {line_count} ligne(s), {char_count} caractere(s) captures")

        compact_prompt = self.compact(prompt, width=100, max_lines=10)
        print(textwrap.indent(compact_prompt, "  "))

        if compact_prompt != prompt.strip():
            print(self.color("  affichage abrege, contenu complet envoye au modele", self.muted))

    def info(self, message: str) -> None:
        print(self.color(f"  {message}", self.cyan))

    def warning(self, message: str) -> None:
        print(self.color(f"  {message}", self.coral))

    def task_start(self, index: int, total: int, task: str) -> None:
        self.section(f"Tache {index}/{total}")
        print(f"  {task}")

    def task_done(self, index: int, note: str) -> None:
        short_note = self.compact(note, width=90, max_lines=4)
        print(self.color(f"  ok tache {index}", self.cyan))
        if short_note:
            print(textwrap.indent(short_note, "  "))

    def tool_call(self, name: str, arguments: Any) -> None:
        print()
        print(self.color(f"  outil -> {name}", self.cyan))
        if arguments:
            print(textwrap.indent(self.compact(str(arguments), width=100, max_lines=3), "    "))

    def tool_result(self, result: str) -> None:
        print(self.color("  resultat outil", self.muted))
        print(textwrap.indent(self.compact(result, width=100, max_lines=8), "    "))

    def final_answer(self, answer: str) -> None:
        if not answer:
            return
        self.section("Resultat")
        print(textwrap.indent(answer.strip(), "  "))
        print()

    def permission(self, action: str, scope: str, mode: str) -> str:
        """Demande une autorisation et retourne la reponse brute."""

        self.section("Autorisation")
        print(f"  Action : {action}")
        print(f"  Portee : {scope}")

        if mode == "session":
            return input("  Autoriser pour cette session ? [y/N] ").strip().lower()

        return input("  [y] une fois  [s] session  [n] non > ").strip().lower()

    def compact(self, text: str, width: int = 88, max_lines: int = 6) -> str:
        """Raccourcit un texte long pour l'affichage CLI."""

        lines = text.strip().splitlines()
        if not lines:
            return ""

        visible = []
        for line in lines[:max_lines]:
            visible.append(textwrap.shorten(line, width=width, placeholder="..."))

        if len(lines) > max_lines:
            visible.append("...")

        return "\n".join(visible)
