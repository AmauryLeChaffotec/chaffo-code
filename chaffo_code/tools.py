from __future__ import annotations

import ast
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
    - remplacer une plage de lignes dans un fichier ;
    - corriger certains UnboundLocalError Python simples ;
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

    def schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """Schemas envoyes a Ollama dans le champ `tools`."""

        if names is None:
            return [tool.schema for tool in self.tools.values()]

        wanted = set(names)
        return [tool.schema for name, tool in self.tools.items() if name in wanted]

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
        end_line: int | None = None,
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
        if end_line is not None:
            end = min(end_line, len(lines))
        else:
            end = min(start + max_lines - 1, len(lines))

        if start > len(lines):
            return f"Le fichier a seulement {len(lines)} lignes."
        if end < start:
            return "Erreur: end_line doit etre superieur ou egal a start_line."

        numbered = [
            f"{line_number:4}: {lines[line_number - 1]}"
            for line_number in range(start, end + 1)
        ]
        return "\n".join(numbered)

    def insert_lines(self, path: str, after_line: int, content: str) -> str:
        """Insere du contenu apres une ligne donnee."""

        file_path = self._safe_path(path)
        if not file_path.exists() or not file_path.is_file():
            return f"Fichier introuvable: {path}"

        text = file_path.read_text(encoding="utf-8", errors="replace")
        had_final_newline = text.endswith("\n")
        lines = text.splitlines()

        if after_line < 0 or after_line > len(lines):
            return f"Erreur: after_line doit etre entre 0 et {len(lines)}."

        action = f"inserer du contenu apres la ligne {after_line} dans {self._relative(file_path)}"
        if not self._confirm(action, scope="files:write"):
            return "Action annulee par l'utilisateur."

        insertion = content.splitlines()
        new_lines = lines[:after_line] + insertion + lines[after_line:]
        output = "\n".join(new_lines)
        if had_final_newline:
            output += "\n"
        file_path.write_text(output, encoding="utf-8")

        preview_start = max(after_line - 3, 1)
        preview_lines = self.read_file(
            path,
            start_line=preview_start,
            max_lines=len(insertion) + 8,
        )
        return (
            f"Contenu insere dans {self._relative(file_path)} apres la ligne {after_line}.\n"
            f"Apercu:\n{preview_lines}"
        )

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

    def replace_lines(
        self,
        path: str,
        start_line: int,
        end_line: int,
        new_content: str,
    ) -> str:
        """Remplace une plage de lignes 1-indexee dans un fichier.

        Cet outil est plus simple pour un coding agent que `replace_in_file`,
        car il peut s'appuyer directement sur les numeros retournes par
        `read_file`.
        """

        file_path = self._safe_path(path)
        if not file_path.exists() or not file_path.is_file():
            return f"Fichier introuvable: {path}"

        if start_line < 1 or end_line < start_line:
            return "Erreur: start_line doit etre >= 1 et end_line doit etre >= start_line."

        text = file_path.read_text(encoding="utf-8", errors="replace")
        had_final_newline = text.endswith("\n")
        lines = text.splitlines()

        if start_line > len(lines):
            return f"Erreur: le fichier a seulement {len(lines)} lignes."
        if end_line > len(lines):
            return f"Erreur: end_line depasse la longueur du fichier ({len(lines)} lignes)."

        replacement_lines = new_content.splitlines()
        new_lines = lines[: start_line - 1] + replacement_lines + lines[end_line:]

        action = (
            f"remplacer les lignes {start_line}-{end_line} "
            f"dans {self._relative(file_path)}"
        )
        if not self._confirm(action, scope="files:write"):
            return "Action annulee par l'utilisateur."

        output = "\n".join(new_lines)
        if had_final_newline:
            output += "\n"
        file_path.write_text(output, encoding="utf-8")

        preview_start = max(start_line - 3, 1)
        preview_lines = self.read_file(
            path,
            start_line=preview_start,
            max_lines=len(replacement_lines) + 6,
        )
        return (
            f"Lignes {start_line}-{end_line} remplacees dans {self._relative(file_path)}.\n"
            f"Apercu:\n{preview_lines}"
        )

    def patch_python_unboundlocal(
        self,
        path: str,
        function_name: str,
        variable_name: str,
    ) -> str:
        """Corrige un UnboundLocalError courant avec une declaration global.

        Cas vise :
        - une variable est definie au niveau module ;
        - elle est modifiee dans une fonction avec `=`, `+=`, `*=`, etc. ;
        - Python la considere alors locale et leve UnboundLocalError avant la
          premiere affectation.

        L'outil ajoute ou complete une ligne `global ...` au debut de la
        fonction. Il inclut aussi les autres variables globales modifiees dans
        la meme fonction.
        """

        file_path = self._safe_path(path)
        if not file_path.exists() or not file_path.is_file():
            return f"Fichier introuvable: {path}"

        text = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return f"Erreur: impossible de parser le fichier Python: {exc}"

        function = self._find_function(tree, function_name)
        if function is None:
            return f"Erreur: fonction introuvable: {function_name}"

        module_names = self._module_assigned_names(tree)
        function_names = self._function_assigned_names(function)
        global_names = sorted((module_names & function_names) | {variable_name})

        if not global_names:
            return "Erreur: aucune variable globale candidate trouvee."

        lines = text.splitlines()
        existing_global = self._first_global_statement(function)
        global_line = " " * (function.col_offset + 4) + "global " + ", ".join(global_names)

        if existing_global is not None:
            old_line_number = existing_global.lineno
            old_line = lines[old_line_number - 1].strip()
            existing_names = set(existing_global.names)
            merged_names = sorted(existing_names | set(global_names))
            global_line = " " * (function.col_offset + 4) + "global " + ", ".join(merged_names)
            action = (
                f"mettre a jour `{old_line}` en `{global_line.strip()}` "
                f"dans {self._relative(file_path)}"
            )
            if not self._confirm(action, scope="files:write"):
                return "Action annulee par l'utilisateur."
            lines[old_line_number - 1] = global_line
            changed_line = old_line_number
        else:
            insert_after = self._global_insert_after_line(function)
            action = (
                f"ajouter `{global_line.strip()}` apres la ligne {insert_after} "
                f"dans {self._relative(file_path)}"
            )
            if not self._confirm(action, scope="files:write"):
                return "Action annulee par l'utilisateur."
            lines.insert(insert_after, global_line)
            changed_line = insert_after + 1

        output = "\n".join(lines)
        if text.endswith("\n"):
            output += "\n"
        file_path.write_text(output, encoding="utf-8")

        preview = self.read_file(
            path,
            start_line=max(changed_line - 4, 1),
            max_lines=10,
        )
        return (
            f"Patch UnboundLocalError applique dans {self._relative(file_path)}.\n"
            f"Variables globales declarees: {', '.join(global_names)}\n"
            f"Apercu:\n{preview}"
        )

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
                                "end_line": {
                                    "type": "integer",
                                    "description": "Derniere ligne a lire, optionnelle.",
                                },
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
            "insert_lines": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "insert_lines",
                        "description": "Insere du contenu apres une ligne donnee dans un fichier.",
                        "parameters": {
                            "type": "object",
                            "required": ["path", "after_line", "content"],
                            "properties": {
                                "path": {"type": "string", "description": "Chemin du fichier."},
                                "after_line": {
                                    "type": "integer",
                                    "description": "Ligne apres laquelle inserer. Utilise 0 pour le debut du fichier.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Contenu a inserer, sans numeros de lignes.",
                                },
                            },
                        },
                    },
                },
                handler=self.insert_lines,
            ),
            "replace_in_file": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "replace_in_file",
                        "description": (
                            "Remplace un texte exact par un autre dans un fichier. "
                            "A utiliser seulement si le texte exact est connu. "
                            "Pour modifier du code par numeros de lignes, preferer replace_lines."
                        ),
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
            "replace_lines": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "replace_lines",
                        "description": (
                            "Remplace une plage de lignes dans un fichier. "
                            "Utilise cet outil pour les corrections de code apres read_file."
                        ),
                        "parameters": {
                            "type": "object",
                            "required": ["path", "start_line", "end_line", "new_content"],
                            "properties": {
                                "path": {"type": "string", "description": "Chemin du fichier."},
                                "start_line": {
                                    "type": "integer",
                                    "description": "Premiere ligne a remplacer, 1-indexee.",
                                },
                                "end_line": {
                                    "type": "integer",
                                    "description": "Derniere ligne a remplacer, incluse.",
                                },
                                "new_content": {
                                    "type": "string",
                                    "description": (
                                        "Nouveau contenu sans numeros de lignes. "
                                        "Peut contenir plusieurs lignes."
                                    ),
                                },
                            },
                        },
                    },
                },
                handler=self.replace_lines,
            ),
            "patch_python_unboundlocal": Tool(
                schema={
                    "type": "function",
                    "function": {
                        "name": "patch_python_unboundlocal",
                        "description": (
                            "Corrige un UnboundLocalError Python courant en ajoutant "
                            "une declaration global dans la fonction concernee."
                        ),
                        "parameters": {
                            "type": "object",
                            "required": ["path", "function_name", "variable_name"],
                            "properties": {
                                "path": {"type": "string", "description": "Chemin du fichier Python."},
                                "function_name": {
                                    "type": "string",
                                    "description": "Nom de la fonction du traceback, par exemple main.",
                                },
                                "variable_name": {
                                    "type": "string",
                                    "description": "Nom de la variable du UnboundLocalError.",
                                },
                            },
                        },
                    },
                },
                handler=self.patch_python_unboundlocal,
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

    def _find_function(self, tree: ast.AST, function_name: str) -> ast.FunctionDef | None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                return node
        return None

    def _module_assigned_names(self, tree: ast.Module) -> set[str]:
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                names.update(self._assigned_names_from_node(node))
        return names

    def _function_assigned_names(self, function: ast.FunctionDef) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(function):
            if node is function:
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                names.update(self._assigned_names_from_node(node))
        return names

    def _assigned_names_from_node(self, node: ast.AST) -> set[str]:
        names: set[str] = set()
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(self._assigned_names_from_target(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(self._assigned_names_from_target(node.target))
        elif isinstance(node, ast.AugAssign):
            names.update(self._assigned_names_from_target(node.target))
        return names

    def _assigned_names_from_target(self, target: ast.AST) -> set[str]:
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            names: set[str] = set()
            for item in target.elts:
                names.update(self._assigned_names_from_target(item))
            return names
        return set()

    def _first_global_statement(self, function: ast.FunctionDef) -> ast.Global | None:
        for node in function.body:
            if isinstance(node, ast.Global):
                return node
        return None

    def _global_insert_after_line(self, function: ast.FunctionDef) -> int:
        if (
            function.body
            and isinstance(function.body[0], ast.Expr)
            and isinstance(function.body[0].value, ast.Constant)
            and isinstance(function.body[0].value.value, str)
        ):
            return function.body[0].end_lineno or function.body[0].lineno

        return function.lineno
