from __future__ import annotations

import unicodedata
from typing import Any

from .config import AgentConfig
from .ollama_client import OllamaClient
from .tools import ToolRegistry
from .ui import Console


SYSTEM_PROMPT = """Tu es Chaffo code, un coding agent local lance en CLI.

Ta mission:
- aider l'utilisateur a comprendre, modifier et tester du code dans le workspace;
- avancer par petites etapes;
- utiliser les outils quand tu as besoin de lire, modifier ou executer quelque chose;
- expliquer clairement ce que tu as fait a la fin.

Regles importantes:
- Lis les fichiers avant de les modifier.
- Tous les chemins passes aux outils sont relatifs au workspace actif.
- Si un traceback mentionne un fichier dans le workspace actif, utilise son chemin relatif.
- Prefere des modifications simples et localisees.
- N'invente pas le contenu d'un fichier: utilise read_file si tu dois t'appuyer dessus.
- Pour modifier du code, prefere replace_lines apres avoir lu les numeros de lignes.
- N'inclus jamais les numeros de lignes de read_file dans le contenu ecrit.
- Les actions d'ecriture et d'execution peuvent demander confirmation a l'utilisateur.
- Suis le plan fourni par le harness, tache par tache.
- Si un outil echoue, ne dis pas que la tache est terminee: corrige ton appel ou essaie une autre approche.
- Si une commande echoue, lis l'erreur et propose ou applique une correction.
- Ignore les UserWarning non bloquants si un traceback fatal suit.
- Pour UnboundLocalError sur une variable locale, cherche une assignation dans la meme fonction avant de conclure.
- Quand la tache est terminee, reponds sans appeler d'outil supplementaire.
"""


class ChaffoAgent:
    """Boucle agentique principale.

    Le modele peut appeler un ou plusieurs outils. Apres chaque appel, on ajoute
    le resultat au fil de messages, puis on redemande au modele quoi faire.
    La boucle s'arrete quand le modele repond sans tool call.
    """

    def __init__(
        self,
        config: AgentConfig,
        client: OllamaClient,
        tools: ToolRegistry,
        console: Console | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.tools = tools
        self.console = console or Console()
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

    def ask(self, user_prompt: str) -> str:
        """Traite une demande utilisateur et retourne la reponse finale."""

        self.console.user_prompt(user_prompt)
        tasks = self._build_plan(user_prompt)
        self.console.plan(tasks)

        self.messages.append(
            {
                "role": "user",
                "content": (
                    f"Demande initiale:\n{user_prompt}\n\n"
                    "Plan choisi par le harness:\n"
                    + "\n".join(f"{index}. {task}" for index, task in enumerate(tasks, start=1))
                ),
            }
        )

        task_notes: list[str] = []
        for index, task in enumerate(tasks, start=1):
            self.console.task_start(index, len(tasks), task)
            required_tools = self._required_tools_for_task(task)
            task_prompt = (
                f"Execute uniquement la tache {index}/{len(tasks)}:\n{task}\n\n"
                f"Workspace actif: {self.config.workspace}\n"
                "Tous les chemins d'outils doivent etre relatifs a ce workspace.\n"
                f"Outils requis pour cette tache: {', '.join(required_tools) or 'aucun'}.\n"
                "Utilise les outils si necessaire. Si un outil renvoie Erreur, "
                "Fichier introuvable, Texte introuvable, Action annulee ou code_sortie non nul, "
                "ne conclus pas au succes: corrige ton approche ou explique le blocage.\n"
                "Quand cette tache est vraiment terminee, donne un court bilan et n'appelle plus d'outil."
            )
            note = self._run_tool_loop(task_prompt, required_tools=required_tools)
            task_notes.append(note)
            self.console.task_done(index, note)

        return self._summarize(user_prompt, tasks, task_notes)

    def _build_plan(self, user_prompt: str) -> list[str]:
        """Demande au modele un plan court, puis le parse simplement."""

        if self._is_simple_answer_request(user_prompt):
            return [user_prompt]

        plan_prompt = (
            "Construis un plan d'execution pour un coding agent CLI.\n"
            "Retourne uniquement une liste numerotee de 1 a 5 taches courtes.\n"
            "Chaque tache doit etre concrete et actionnable.\n\n"
            f"Workspace actif: {self.config.workspace}\n"
            "Tous les chemins d'outils sont relatifs a ce workspace.\n\n"
            "Regles de planification:\n"
            "- Pour une demande simple, fais une seule tache.\n"
            "- Pour une modification de code, prevois inspection, modification, verification.\n"
            "- Pour une traceback Python, lis le fichier mentionne, inspecte autour de la ligne, corrige, puis teste.\n"
            "- Ignore les UserWarning non bloquants si un traceback fatal suit.\n"
            "- N'ajoute pas d'inspection, de modification ou de test si la demande ne l'exige pas.\n"
            "- Ne cree pas de tache vague comme 'finaliser' si elle n'apporte rien.\n\n"
            f"Demande utilisateur:\n{user_prompt}"
        )

        response = self.client.chat(
            model=self.config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": plan_prompt},
            ],
            tools=[],
        )
        content = str(response.get("message", {}).get("content", "")).strip()
        tasks = self._parse_plan(content)
        return tasks or [user_prompt]

    def _is_simple_answer_request(self, user_prompt: str) -> bool:
        """Evite un gros plan agentique pour une simple reponse texte."""

        prompt = user_prompt.strip().lower()
        simple_starts = ("reponds", "repond ", "dis ", "dit ", "ecris juste")
        coding_markers = (
            ".py",
            "traceback",
            "erreur",
            "exception",
            "corrige",
            "modifie",
            "cree",
            "ajoute",
            "supprime",
            "fichier",
            "lance",
            "execute",
            "test",
        )

        return prompt.startswith(simple_starts) and not any(
            marker in prompt for marker in coding_markers
        )

    def _parse_plan(self, content: str) -> list[str]:
        """Parse une liste numerotee sans chercher a etre trop malin."""

        tasks: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            cleaned = self._remove_number_prefix(line)
            if cleaned:
                tasks.append(cleaned)

        return tasks[:5]

    def _remove_number_prefix(self, line: str) -> str:
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            return ""

        prefix = parts[0].rstrip(".):")
        if prefix.isdigit():
            return parts[1].strip()

        if line.startswith("- "):
            return line[2:].strip()

        return ""

    def _required_tools_for_task(self, task: str) -> list[str]:
        """Retourne les outils que le modele doit appeler pour une tache."""

        lowered = self._normalize_text(task)

        read_markers = (
            "lire",
            "inspecter",
            "analyser",
            "comprendre",
            "identifier",
            "trouver",
            "chercher",
            "contexte",
        )
        write_markers = (
            "modifier",
            "corriger",
            "ajouter",
            "initialiser",
            "remplacer",
            "supprimer",
            "ecrire",
            "creer",
        )
        run_markers = (
            "executer",
            "lancer",
            "tester",
            "verifier",
            "confirmer",
        )

        required: list[str] = []
        if any(marker in lowered for marker in read_markers):
            required.extend(["read_file", "list_files"])
        if any(marker in lowered for marker in write_markers):
            required.extend(["replace_lines", "replace_in_file", "write_file"])
        if any(marker in lowered for marker in run_markers):
            required.append("run_command")

        return list(dict.fromkeys(required))

    def _normalize_text(self, value: str) -> str:
        """Minuscule + suppression des accents pour matcher simplement."""

        normalized = unicodedata.normalize("NFKD", value.lower())
        return normalized.encode("ascii", "ignore").decode("ascii")

    def _run_tool_loop(
        self,
        task_prompt: str,
        required_tools: list[str] | None = None,
    ) -> str:
        """Execute une tache avec la boucle modele -> outils -> modele."""

        required_tools = required_tools or []
        self.messages.append({"role": "user", "content": task_prompt})
        last_tool_result = ""
        failure_reminder_sent = False
        tool_requirement_reminders = 0
        used_tools: list[str] = []

        for step in range(1, self.config.max_steps + 1):
            if self.config.verbose:
                self.console.section(f"Boucle outil {step}/{self.config.max_steps}")

            response = self.client.chat(
                model=self.config.model,
                messages=self.messages,
                tools=self.tools.schemas(),
            )

            assistant_message = response.get("message", {})
            tool_calls = assistant_message.get("tool_calls") or []

            # On conserve le message assistant pour que le modele garde le fil.
            self.messages.append(assistant_message)

            if not tool_calls:
                content = str(assistant_message.get("content", "")).strip()
                if required_tools and not self._used_required_tool(used_tools, required_tools):
                    if tool_requirement_reminders < 2:
                        tool_requirement_reminders += 1
                        self.messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Tu ne peux pas terminer cette tache sans appeler un outil requis.\n"
                                    f"Outils requis: {', '.join(required_tools)}\n"
                                    f"Outils deja utilises: {', '.join(used_tools) or 'aucun'}\n"
                                    "Appelle maintenant un outil requis avec les bons arguments."
                                ),
                            }
                        )
                        continue

                    return (
                        "Bloque: le modele a tente de terminer la tache sans appeler "
                        f"un outil requis ({', '.join(required_tools)})."
                    )

                if (
                    last_tool_result
                    and self._tool_result_failed(last_tool_result)
                    and not failure_reminder_sent
                ):
                    failure_reminder_sent = True
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Le dernier resultat d'outil indique un echec:\n"
                                f"{last_tool_result}\n\n"
                                "Ne conclus pas que la tache est reussie. "
                                "Corrige l'appel d'outil, essaie une autre approche, "
                                "ou explique clairement pourquoi c'est bloque."
                            ),
                        }
                    )
                    continue

                if used_tools:
                    return f"{content}\n\nOutils utilises: {', '.join(used_tools)}"

                return content

            for call in tool_calls:
                tool_name, arguments = self._parse_tool_call(call)
                used_tools.append(tool_name)
                self.console.tool_call(tool_name, arguments)

                result = self.tools.execute(tool_name, arguments)
                last_tool_result = result
                failure_reminder_sent = False

                if self.config.verbose or self._tool_result_failed(result):
                    self.console.tool_result(result)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": result,
                    }
                )

        return (
            "J'ai atteint la limite d'etapes sans terminer. "
            "Relance avec --max-steps plus grand si necessaire."
        )

    def _used_required_tool(
        self,
        used_tools: list[str],
        required_tools: list[str],
    ) -> bool:
        """Verifie qu'au moins un outil requis a vraiment ete appele."""

        return any(tool in required_tools for tool in used_tools)

    def _tool_result_failed(self, result: str) -> bool:
        """Detecte les echecs d'outils les plus courants."""

        lowered = result.lower()
        failure_markers = (
            "erreur",
            "fichier introuvable",
            "texte a remplacer introuvable",
            "action annulee",
            "commande bloquee",
        )
        if any(marker in lowered for marker in failure_markers):
            return True

        for line in result.splitlines():
            if line.startswith("code_sortie:"):
                return line.strip() != "code_sortie: 0"

        return False

    def _summarize(
        self,
        user_prompt: str,
        tasks: list[str],
        task_notes: list[str],
    ) -> str:
        """Produit une synthese finale concise apres toutes les taches."""

        summary_prompt = (
            "Resume le travail effectue pour l'utilisateur.\n"
            "Sois concis: ce qui a ete fait, fichiers touches si tu les connais, "
            "et prochaine action utile si necessaire.\n\n"
            f"Demande initiale:\n{user_prompt}\n\n"
            "Plan:\n"
            + "\n".join(f"{index}. {task}" for index, task in enumerate(tasks, start=1))
            + "\n\nBilans de taches:\n"
            + "\n".join(f"{index}. {note}" for index, note in enumerate(task_notes, start=1))
        )

        response = self.client.chat(
            model=self.config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": summary_prompt},
            ],
            tools=[],
        )
        return str(response.get("message", {}).get("content", "")).strip()

    def set_workspace(self, workspace: Any) -> None:
        """Change le workspace de l'agent et garde une trace dans le contexte."""

        self.config.workspace = workspace
        self.tools.set_workspace(workspace)
        self.add_system_note(
            f"Le workspace actif est maintenant: {workspace}. "
            "Tous les chemins d'outils sont relatifs a ce dossier."
        )

    def add_system_note(self, content: str) -> None:
        """Ajoute une note de contexte invisible dans le fil agentique."""

        self.messages.append(
            {
                "role": "system",
                "content": content,
            }
        )

    def _parse_tool_call(self, call: dict[str, Any]) -> tuple[str, Any]:
        """Extrait le nom et les arguments d'un tool call Ollama."""

        function = call.get("function", {})
        name = str(function.get("name", ""))
        arguments = function.get("arguments", {})
        return name, arguments
