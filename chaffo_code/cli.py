from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .agent import ChaffoAgent
from .config import AgentConfig
from .ollama_client import OllamaClient, OllamaError
from .tools import ToolRegistry
from .ui import Console


def main() -> None:
    args = parse_args()
    workspace = Path(args.workspace).resolve()

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

    prompt = " ".join(args.prompt).strip()
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
        help="Dossier dans lequel l'agent peut lire et modifier les fichiers.",
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
    print("Tape `exit` ou `quit` pour quitter.\n")

    while True:
        try:
            prompt = input("chaffo> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if prompt.lower() in {"exit", "quit"}:
            return
        if not prompt:
            continue

        answer = agent.ask(prompt)
        console.final_answer(answer)
