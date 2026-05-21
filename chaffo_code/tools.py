from __future__ import annotations

import fnmatch
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .ui import Console


MAX_TOOL_OUTPUT_CHARS = 12_000


@dataclass
class Tool:
    """Un outil expose au modele.

    - schema: la description JSON envoyee a Ollama.
    - handler: la fonction Python executee quand le modele appelle l'outil.
    """

    schema: dict[str, Any]
    handler: Callable[..., str]


class ToolRegistry:
    """Regroupe les outils de Chaffo code.

    Les outils sont volontairement simples :
    - lister les fichiers ;
    - lire un fichier ;
    - ecrire un fichier ;
    - remplacer du texte dans un fichier ;
    - lancer une commande.

    Chaque chemin est limite au workspace choisi par l'utilisateur. Les actions
    qui modifient quelque chose demandent une confirmation. En mode session,
    une autorisation est retenue pour limiter les prompts repetitifs.
    """

    def __init__(
        self,
        workspace: Path,
        auto_approve: bool = False,
        permission_mode: str = "session",
        console: Console | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.permission_mode = "auto" if auto_approve else permission_mode
        self.session_approvals: set[str] = set()
        self.console = console or Console()
        self.tools = self._build_tools()

    def set_workspace(self, workspace: Path) -> None:
        """Change le workspace utilise par les outils.

        Le CLI verifie deja que le chemin reste dans `workspaces/`.
        Cette methode permet simplement au REPL de changer de projet sans
        relancer Chaffo code.
        """

        self.workspace = workspace.resolve()

    def schemas(self) -> list[dict[str, Any]]:
        """Schemas envoyes a Ollama dans le champ `tools`."""

        return [tool.schema for tool in self.tools.values()]

    def execute(self, name: str, arguments: Any) -> str:
        """Execute l'outil demande par le modele."""

        if name not in self.tools:
            return f"Erreur: outil inconnu `{name}`."

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return f"Erreur: arguments JSON invalides pour `{name}`: {arguments}"

        if arguments is None:
            arguments = {}

        if not isinstance(arguments, dict):
            return f"Erreur: les arguments de `{name}` doivent etre un objet JSON."

        try:
            result = self.tools[name].handler(**arguments)
        except TypeError as exc:
            return f"Erreur: mauvais arguments pour `{name}`: {exc}"
        except Exception as exc:  # Le tool result doit revenir au modele.
            return f"Erreur pendant `{name}`: {exc}"

        return self._trim(result)

    def list_files(
        self,
        path: str = ".",
        pattern: str = "*",
        max_results: int = 200,
    ) -> str:
        """Liste les fichiers du workspace."""

        root = self._safe_path(path)
        if not root.exists():
            return f"Le chemin n'existe pas: {path}"
        if not root.is_dir():
            return f"Le chemin n'est pas un dossier: {path}"

        ignored_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules"}
        matches: list[str] = []

        for item in root.rglob("*"):
            if any(part in ignored_dirs for part in item.parts):
                continue
            if item.is_file() and fnmatch.fnmatch(item.name, pattern):
                matches.append(self._relative(item))
            if len(matches) >= max_results:
                break

        if not matches:
            return "Aucun fichier trouve."

        return "\n".join(matches)

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        max_lines: int = 200,
    ) -> str:
        """Lit une portion de fichier avec des numeros de ligne."""

        file_path = self._safe_path(path)
        if not file_path.exists():
            return f"Le fichier n'existe pas: {path}"
        if not file_path.is_file():
            return f"Ce chemin n'est pas un fichier: {path}"

        text = file_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(start_line, 1)
        end = min(start + max_lines - 1, len(lines))

        if start > len(lines):
            return f"Le fichier a seulement {len(lines)} lignes."

        numbered = [
            f"{line_number:4}: {lines[line_number - 1]}"
            for line_number in range(start, end + 1)
        ]
        return "\n".join(numbered)

    def write_file(self, path: str, content: str) -> str:
        """Cree ou remplace un fichier apres confirmation."""

        file_path = self._safe_path(path)
        action = f"ecrire {self._relative(file_path)} ({len(content)} caracteres)"

        if not self._confirm(action, scope="files:write"):
            return "Action annulee par l'utilisateur."

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Fichier ecrit: {self._relative(file_path)}"

    def replace_in_file(self, path: str, old: str, new: str) -> str:
        """Remplace un texte exact dans un fichier."""

        file_path = self._safe_path(path)
        if not file_path.exists() or not file_path.is_file():
            return f"Fichier introuvable: {path}"

        text = file_path.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            return "Texte a remplacer introuvable."

        action = f"remplacer {count} occurrence(s) dans {self._relative(file_path)}"
        if not self._confirm(action, scope="files:write"):
            return "Action annulee par l'utilisateur."

        file_path.write_text(text.replace(old, new), encoding="utf-8")
        return f"Remplacement termine dans {self._relative(file_path)} ({count} occurrence(s))."

    def run_command(self, command: str, timeout_seconds: int = 30) -> str:
        """Lance une commande dans le workspace apres confirmation."""

        blocked_reason = self._blocked_command_reason(command)
        if blocked_reason:
            return f"Commande bloquee: {blocked_reason}"

        command_scope = f"command:{self._command_name(command)}"
        action = f"executer `{command}` dans {self.workspace}"
        if not self._confirm(action, scope=command_scope):
            return "Action annulee par l'utilisateur."

        completed = subprocess.run(
            command,
            cwd=self.workspace,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )

        output = [
            f"code_sortie: {completed.returncode}",
            "--- stdout ---",
            completed.stdout.strip(),
            "--- stderr ---",
            completed.stderr.strip(),
        ]
        return "\n".join(output)

    def _build_tools(self) -> dict[str, Tool]:
        return {
            "list_files": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "list_files",
                        "description": "Liste les fichiers du workspace.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Dossier a explorer, relatif au workspace.",
                                },
                                "pattern": {
                                    "type": "string",
                                    "description": "Filtre de nom, par exemple *.py ou *.md.",
                                },
                                "max_results": {
                                    "type": "integer",
                                    "description": "Nombre maximum de fichiers retournes.",
                                },
                            },
                        },
                    },
                },
                handler=self.list_files,
            ),
            "read_file": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Lit un fichier du workspace avec numeros de ligne.",
                        "parameters": {
                            "type": "object",
                            "required": ["path"],
                            "properties": {
                                "path": {"type": "string", "description": "Chemin du fichier."},
                                "start_line": {"type": "integer", "description": "Premiere ligne a lire."},
                                "max_lines": {"type": "integer", "description": "Nombre maximum de lignes."},
                            },
                        },
                    },
                },
                handler=self.read_file,
            ),
            "write_file": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "description": "Cree ou remplace un fichier dans le workspace.",
                        "parameters": {
                            "type": "object",
                            "required": ["path", "content"],
                            "properties": {
                                "path": {"type": "string", "description": "Chemin du fichier."},
                                "content": {"type": "string", "description": "Contenu complet du fichier."},
                            },
                        },
                    },
                },
                handler=self.write_file,
            ),
            "replace_in_file": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "replace_in_file",
                        "description": "Remplace un texte exact par un autre dans un fichier.",
                        "parameters": {
                            "type": "object",
                            "required": ["path", "old", "new"],
                            "properties": {
                                "path": {"type": "string", "description": "Chemin du fichier."},
                                "old": {"type": "string", "description": "Texte exact a remplacer."},
                                "new": {"type": "string", "description": "Nouveau texte."},
                            },
                        },
                    },
                },
                handler=self.replace_in_file,
            ),
            "run_command": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "description": "Execute une commande shell dans le workspace.",
                        "parameters": {
                            "type": "object",
                            "required": ["command"],
                            "properties": {
                                "command": {"type": "string", "description": "Commande a executer."},
                                "timeout_seconds": {
                                    "type": "integer",
                                    "description": "Delai maximum avant interruption.",
                                },
                            },
                        },
                    },
                },
                handler=self.run_command,
            ),
        }

    def _safe_path(self, path: str) -> Path:
        """Convertit un chemin relatif en chemin absolu limite au workspace."""

        candidate = (self.workspace / path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError("chemin hors du workspace refuse")
        return candidate

    def _relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.workspace))

    def _confirm(self, action: str, scope: str) -> bool:
        """Demande une autorisation selon le mode choisi.

        Modes:
        - auto: aucune question;
        - session: une fois par portee, puis c'est retenu;
        - ask: question a chaque fois, avec option session.
        """

        if self.permission_mode == "auto":
            return True

        if scope in self.session_approvals:
            return True

        answer = self.console.permission(action, scope, self.permission_mode)
        accepted = answer in {"y", "yes", "o", "oui", "s", "session"}

        if not accepted:
            if answer:
                self.console.warning("Reponse non reconnue, action refusee.")
            return False

        if self.permission_mode == "session" or answer in {"s", "session"}:
            self.session_approvals.add(scope)

        return True

    def _command_name(self, command: str) -> str:
        """Extrait un nom court de commande pour les autorisations session."""

        try:
            tokens = shlex.split(command, posix=False)
        except ValueError:
            tokens = command.split()

        if not tokens:
            return "unknown"

        return Path(tokens[0]).name.lower()

    def _blocked_command_reason(self, command: str) -> str | None:
        """Bloque quelques commandes dangereuses faciles a reconnaitre."""

        lowered = command.lower()
        dangerous_parts = [
            "rm -rf",
            "remove-item -recurse",
            "del /s",
            "format ",
            "shutdown",
            "git reset --hard",
        ]
        for part in dangerous_parts:
            if part in lowered:
                return f"`{part}` est considere dangereux"

        try:
            tokens = shlex.split(command, posix=False)
        except ValueError:
            tokens = []

        if tokens and tokens[0].lower() in {"rm", "rmdir"}:
            return "suppression via commande shell non autorisee dans ce projet pedagogique"

        return None

    def _trim(self, value: str) -> str:
        if len(value) <= MAX_TOOL_OUTPUT_CHARS:
            return value
        return value[:MAX_TOOL_OUTPUT_CHARS] + "\n... sortie tronquee ..."
