from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentConfig:
    """Configuration simple partagee par le CLI, l'agent et les outils."""

    model: str = "gemma4:e2b"
    base_url: str = "http://localhost:11434/api"
    workspace: Path = Path.cwd()
    max_steps: int = 8
    auto_approve: bool = False
    permission_mode: str = "session"
    verbose: bool = False

    def normalized_base_url(self) -> str:
        """Retourne l'URL de base sans slash final."""

        return self.base_url.rstrip("/")
