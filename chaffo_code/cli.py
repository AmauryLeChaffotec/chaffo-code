from __future__ import annotations

import argparse
import os
import subprocess
import shlex
import sys
from pathlib import Path

from .agent import ChaffoAgent
from .config import AgentConfig
from .ollama_client import OllamaClient, OllamaError
from .tools import ToolRegistry
from .ui import Console


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACES_ROOT = PROJECT_ROOT / "workspaces"


def main() -> None:
    args = parse_args()
    workspace = resolve_workspace(args.workspace)

    config = AgentConfig(
        model=args.model,
        base_url=args.base_url,
        workspace=workspace,
        max_steps=args.max_steps,
        auto_approve=args.yes,
        permission_mode="auto" if args.yes else args.permission_mode,
        verbose=args.verbose,
    )
    console = Console(use_color=not args.no_color)

    client = OllamaClient(config.normalized_base_url())

    try:
        if args.models:
            print_models(client)
            return

        version = client.get_version()
        if args.check:
            print(f"Ollama repond bien. Version: {version}")
            return
    except OllamaError as exc:
        print(f"Erreur Ollama: {exc}", file=sys.stderr)
        print("Aide: verifie que Ollama tourne puis lance `ollama run gemma4:e2b`.", file=sys.stderr)
        raise SystemExit(1)

    tools = ToolRegistry(
        workspace=config.workspace,
        auto_approve=config.auto_approve,
        permission_mode=config.permission_mode,
        console=console,
    )
    agent = ChaffoAgent(config=config, client=client, tools=tools, console=console)

    prompt = build_prompt(args)
    if prompt:
        answer = agent.ask(prompt)
        console.final_answer(answer)
        return

    run_repl(agent, config, console)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="chaffo-code",
        description="Chaffo code: petit coding agent local avec Ollama.",
    )
    parser.add_argument("prompt", nargs="*", help="Demande a envoyer a l'agent.")
    parser.add_argument(
        "--model",
        default=os.getenv("CHAFFO_MODEL", "gemma4:e2b"),
        help="Modele Ollama a utiliser. Defaut: gemma4:e2b.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/api"),
        help="URL de base de l'API Ollama.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Sous-dossier de workspaces/ dans lequel l'agent travaille. Defaut: workspaces/.",
    )
    parser.add_argument(
        "--prompt-file",
        action="append",
        default=[],
        help="Ajoute le contenu d'un fichier a la demande. Peut etre utilise plusieurs fois.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Lit aussi le contenu envoye via stdin.",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Lit aussi le contenu du presse-papiers.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Nombre maximum d'allers-retours modele/outils.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Autorise automatiquement les ecritures et commandes.",
    )
    parser.add_argument(
        "--permission-mode",
        choices=["session", "ask", "auto"],
        default="session",
        help="Mode d'autorisation. session = une fois par portee. Defaut: session.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Desactive les couleurs ANSI.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche les resultats d'outils pour deboguer la boucle.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verifie seulement que Ollama repond.",
    )
    parser.add_argument(
        "--models",
        action="store_true",
        help="Liste les modeles Ollama disponibles localement.",
    )
    return parser.parse_args()


def resolve_workspace(workspace: str) -> Path:
    """Retourne un workspace force dans le dossier workspaces/.

    Le but est d'eviter que l'agent modifie le code de Chaffo code par erreur.
    `--workspace demo` pointe donc vers `workspaces/demo`.
    `--workspace .` pointe vers `workspaces/`.
    """

    root = WORKSPACES_ROOT.resolve()
    requested = Path(workspace)

    if workspace == ".":
        current_directory = Path.cwd().resolve()
        if current_directory == root or root in current_directory.parents:
            candidate = current_directory
        else:
            candidate = root
    elif requested.is_absolute():
        candidate = requested.resolve()
    else:
        candidate = (root / requested).resolve()

    if candidate != root and root not in candidate.parents:
        print("Erreur: le workspace doit rester dans le dossier workspaces/.", file=sys.stderr)
        raise SystemExit(2)

    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def build_prompt(args: argparse.Namespace) -> str:
    """Construit la demande depuis les arguments, stdin et fichiers."""

    parts: list[str] = []
    inline_prompt = " ".join(args.prompt).strip()
    if inline_prompt:
        parts.append(inline_prompt)

    if args.stdin:
        stdin_content = sys.stdin.read().strip()
        if stdin_content:
            parts.append(format_large_content("stdin", stdin_content))

    if args.clipboard:
        clipboard_content = read_clipboard().strip()
        if clipboard_content:
            parts.append(format_large_content("presse-papiers", clipboard_content))

    for prompt_file in args.prompt_file:
        path = Path(prompt_file).expanduser().resolve()
        if not path.exists() or not path.is_file():
            print(f"Erreur: fichier introuvable pour --prompt-file: {path}", file=sys.stderr)
            raise SystemExit(2)

        content = path.read_text(encoding="utf-8", errors="replace").strip()
        parts.append(format_large_content(str(path), content))

    return "\n\n".join(parts).strip()


def format_large_content(source: str, content: str) -> str:
    """Encadre un long contenu pour que le modele comprenne sa provenance."""

    return f"Contenu fourni depuis {source}:\n\n```text\n{content}\n```"


def read_clipboard() -> str:
    """Lit le presse-papiers sans dependance externe.

    Tkinter fonctionne souvent avec Python desktop. Le fallback PowerShell est
    pratique sur Windows si Tkinter n'est pas disponible.
    """

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        content = root.clipboard_get()
        root.destroy()
        return content
    except Exception:
        pass

    if os.name == "nt":
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        return completed.stdout

    return ""


def print_models(client: OllamaClient) -> None:
    models = client.list_models()
    if not models:
        print("Aucun modele local trouve. Exemple: `ollama run gemma4:e2b`.")
        return

    print("Modeles Ollama disponibles:")
    for model in models:
        print(f"- {model}")


def run_repl(agent: ChaffoAgent, config: AgentConfig, console: Console) -> None:
    console.banner(config.model, config.workspace)
    print("Tape `exit` ou `quit` pour quitter.")
    print("Tape `/paste` pour coller plusieurs lignes, fin avec une ligne `///`.")
    print("Tape `/clip question optionnelle` pour envoyer le presse-papiers.")
    print("Tape `/file chemin question optionnelle` pour envoyer un fichier long.")
    print("Tape `/workspace nom` pour changer de sous-dossier dans workspaces/.\n")

    while True:
        try:
            prompt = input(f"chaffo:{workspace_label(config.workspace)}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if prompt.lower() in {"exit", "quit"}:
            return
        if prompt == "/paste":
            prompt = read_multiline_prompt()
        elif prompt == "/clip" or prompt.startswith("/clip "):
            prompt = read_clipboard_command(prompt)
        elif prompt.startswith("/file "):
            prompt = read_file_command(prompt)
        elif prompt == "/workspace" or prompt.startswith("/workspace "):
            switch_workspace(prompt, agent, config, console)
            continue
        elif looks_like_powershell_prompt(prompt):
            console.warning(
                "Tu as colle une ligne de terminal PowerShell. "
                "Pour envoyer toute l'erreur, utilise `/paste` ou `/clip`."
            )
            continue
        if not prompt:
            continue

        answer = agent.ask(prompt)
        console.final_answer(answer)


def workspace_label(workspace: Path) -> str:
    """Nom court affiche dans le prompt du REPL."""

    try:
        relative = workspace.resolve().relative_to(WORKSPACES_ROOT.resolve())
    except ValueError:
        return "?"

    label = str(relative)
    return "." if label == "." else label


def read_multiline_prompt() -> str:
    """Lit un long prompt multi-ligne dans le REPL."""

    print("Colle ton contenu. Termine avec une ligne qui contient seulement `///`.")
    lines: list[str] = []

    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if line.strip() == "///":
            break

        lines.append(line)

    return "\n".join(lines).strip()


def read_clipboard_command(command: str) -> str:
    """Construit un prompt a partir du presse-papiers."""

    question = command.removeprefix("/clip").strip()
    content = read_clipboard().strip()

    if not content:
        print("Le presse-papiers est vide ou illisible.")
        return ""

    if question:
        return f"{question}\n\n{format_large_content('presse-papiers', content)}"

    return format_large_content("presse-papiers", content)


def read_file_command(command: str) -> str:
    """Lit un fichier depuis le REPL et l'ajoute au prompt."""

    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        print(f"Commande /file invalide: {exc}")
        return ""

    if len(parts) < 2:
        print("Usage: /file chemin [question optionnelle]")
        return ""

    path = Path(parts[1].strip("\"'")).expanduser().resolve()
    question = " ".join(parts[2:]).strip()

    if not path.exists() or not path.is_file():
        print(f"Fichier introuvable: {path}")
        return ""

    content = path.read_text(encoding="utf-8", errors="replace").strip()
    if question:
        return f"{question}\n\n{format_large_content(str(path), content)}"

    return format_large_content(str(path), content)


def switch_workspace(
    command: str,
    agent: ChaffoAgent,
    config: AgentConfig,
    console: Console,
) -> None:
    """Change le workspace courant depuis le REPL."""

    requested = command.removeprefix("/workspace").strip() or "."
    new_workspace = resolve_workspace(requested)
    config.workspace = new_workspace
    agent.set_workspace(new_workspace)
    console.info(f"Workspace actif: {new_workspace}")


def looks_like_powershell_prompt(prompt: str) -> bool:
    r"""Detecte `PS C:\...\workspaces\pong> python pong.py`.

    Ce format indique souvent que l'utilisateur a colle une sortie de terminal
    complete sans passer par /paste. Dans ce cas, seule la premiere ligne serait
    capturee par input(), donc on prefere guider l'utilisateur.
    """

    return prompt.startswith("PS ") and "> " in prompt
