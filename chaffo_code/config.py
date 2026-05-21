from dataclasses import dataclass, field
from pathlib import Path


def default_workspace() -> Path:
    """Workspace par defaut, separe du code source de Chaffo code."""

    return Path(__file__).resolve().parents[1] / "workspaces"


@dataclass
class AgentConfig:
    """Configuration simple partagee par le CLI, l'agent et les outils."""

    model: str = "gemma4:e2b"
    base_url: str = "http://localhost:11434/api"
    workspace: Path = field(default_factory=default_workspace)
    max_steps: int = 8
    auto_approve: bool = False
    permission_mode: str = "session"
    verbose: bool = False

    def normalized_base_url(self) -> str:
        """Retourne l'URL de base sans slash final."""

        return self.base_url.rstrip("/")
