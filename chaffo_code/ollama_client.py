from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    """Erreur lisible quand Ollama n'est pas joignable ou refuse la requete."""


class OllamaClient:
    """Petit client HTTP pour l'API Ollama.

    On utilise uniquement la bibliotheque standard Python pour garder le projet
    facile a comprendre. La doc Ollama indique que l'API locale est exposee sur
    http://localhost:11434/api et que le chat se fait via POST /api/chat.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def get_version(self) -> str:
        """Verifie que le serveur Ollama repond."""

        data = self._request("GET", "/version")
        return str(data.get("version", "version inconnue"))

    def list_models(self) -> list[str]:
        """Retourne les modeles deja disponibles localement."""

        data = self._request("GET", "/tags")
        models = data.get("models", [])
        return [str(model.get("name")) for model in models if model.get("name")]

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Appelle /chat en mode non-streaming.

        Le streaming est tres agreable pour l'utilisateur, mais il complique la
        gestion des tool calls. Pour un projet pedagogique, le mode non-streaming
        est plus clair.
        """

        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }
        return self._request("POST", "/chat", payload)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {"Content-Type": "application/json"}

        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        request = Request(
            url=f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama a retourne HTTP {exc.code}: {details}") from exc
        except URLError as exc:
            raise OllamaError(
                "Impossible de joindre Ollama. Lance `ollama serve` ou ouvre "
                "l'application Ollama, puis verifie l'URL de base."
            ) from exc
        except TimeoutError as exc:
            raise OllamaError("La requete Ollama a depasse le delai autorise.") from exc

