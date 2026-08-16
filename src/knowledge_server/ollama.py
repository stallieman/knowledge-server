from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field


class EmbedResponse(BaseModel):
    """Relevant fields returned by Ollama's embedding endpoint."""

    embeddings: list[list[float]] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    """Relevant fields returned by Ollama's generation endpoint."""

    response: str = ""


@dataclass(frozen=True)
class OllamaClient:
    """HTTP client for the local Ollama service."""

    base_url: str
    chat_model: str
    embedding_model: str
    timeout_seconds: float = 1200.0

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate normalized embeddings for a batch of texts."""
        if not texts:
            return []

        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={
                "model": self.embedding_model,
                "input": texts,
                "truncate": True,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        parsed_response = EmbedResponse.model_validate(response.json())
        if len(parsed_response.embeddings) != len(texts):
            raise RuntimeError("Ollama returned an unexpected embedding count")
        return parsed_response.embeddings

    def generate_answer(self, prompt: str) -> str:
        """Generate a grounded answer with the configured chat model."""
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.chat_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": 0,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 1400,
                    "temperature": 0.1,
                },
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return GenerateResponse.model_validate(response.json()).response.strip()

    def unload_embedding_model(self) -> None:
        """Immediately release the embedding model from RAM and VRAM."""
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.embedding_model,
                "keep_alive": 0,
            },
            timeout=min(self.timeout_seconds, 30.0),
        )
        response.raise_for_status()

    def installed_models(self) -> set[str]:
        """Return model names reported by Ollama."""
        response = httpx.get(
            f"{self.base_url}/api/tags",
            timeout=min(self.timeout_seconds, 30.0),
        )
        response.raise_for_status()
        models = response.json().get("models", [])
        return {str(model.get("name", "")) for model in models}
