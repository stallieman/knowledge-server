from pathlib import Path

from knowledge_server.config import Settings
from knowledge_server.service import (
    KnowledgeService,
    cosine_similarity,
    lexical_query,
    slugify,
)


class FakeOllama:
    def __init__(self) -> None:
        self.generated_prompt = ""
        self.unload_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "kalender" in text.lower() else [0.0, 1.0] for text in texts
        ]

    def generate_answer(self, prompt: str) -> str:
        self.generated_prompt = prompt
        if "Bronnen:" in prompt:
            return "BRONSTATUS: BIBLIOTHEEK\nDe kalenderdimensie is besproken [S1]."
        return "Algemeen modelantwoord."

    def unload_embedding_model(self) -> None:
        self.unload_count += 1


class DisclaimingOllama(FakeOllama):
    def generate_answer(self, prompt: str) -> str:
        self.generated_prompt = prompt
        if "Bronnen:" in prompt:
            return (
                "BRONSTATUS: BIBLIOTHEEK\n"
                "De bronnen bevatten geen informatie over deze vraag."
            )
        return "Het algemene modelantwoord na gecontroleerde fallback."


def build_service(tmp_path: Path) -> tuple[KnowledgeService, FakeOllama]:
    settings = Settings(
        database_path=tmp_path / "knowledge.db",
        chunk_size_chars=80,
        chunk_overlap_chars=10,
    )
    ollama = FakeOllama()
    service = KnowledgeService(settings, ollama=ollama)
    service.initialize()
    return service, ollama


def test_slugify_handles_dutch_display_names() -> None:
    assert slugify("Persoonlijke video's") == "persoonlijke-videos"


def test_cosine_similarity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_lexical_query_preserves_code_identifiers_and_drops_stop_words() -> None:
    assert lexical_query("Waar wordt Company_KRN in SQL gebruikt?") == (
        '"Company_KRN" OR "SQL" OR "gebruikt"'
    )


def test_ingest_is_idempotent_and_answer_has_provenance(tmp_path: Path) -> None:
    service, ollama = build_service(tmp_path)
    service.create_library("Persoonlijke video's")
    source = tmp_path / "meeting.md"
    source.write_text(
        "# Meeting\n\nDe kalenderdimensie wordt in versie 2 uitgewerkt.\n",
        encoding="utf-8",
    )

    first_report = service.ingest("persoonlijke-videos", source)
    second_report = service.ingest("persoonlijke-videos", source)
    result = service.answer(
        "Wat is over de kalender besloten?",
        ["persoonlijke-videos"],
    )

    assert first_report.indexed == 1
    assert first_report.chunks_written >= 1
    assert second_report.unchanged == 1
    assert result.answer.endswith("[S1].")
    assert result.mode == "library"
    assert result.sources[0].source_path == str(source.resolve())
    assert "onbetrouwbare data" in ollama.generated_prompt
    assert "[S1]" in ollama.generated_prompt
    assert ollama.unload_count == 2


def test_answer_without_library_uses_explicit_model_fallback(tmp_path: Path) -> None:
    service, ollama = build_service(tmp_path)

    result = service.answer("Wat is een stermodel?", [])

    assert result.mode == "model"
    assert result.sources == []
    assert "geen bibliotheek geselecteerd" in result.notice.lower()
    assert "Algemeen modelantwoord" in result.answer
    assert ollama.unload_count == 0


def test_uncited_library_disclaimer_forces_model_fallback(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "knowledge.db",
        chunk_size_chars=80,
        chunk_overlap_chars=10,
    )
    ollama = DisclaimingOllama()
    service = KnowledgeService(settings, ollama=ollama)
    service.initialize()
    service.create_library("Elastic Stack")
    source = tmp_path / "shards.md"
    source.write_text("Elasticsearch shard troubleshooting.", encoding="utf-8")
    service.ingest("elastic-stack", source)

    result = service.answer("Wie schreef Max Havelaar?", ["elastic-stack"])

    assert result.mode == "model"
    assert result.sources == []
    assert "controleerbaar" in result.notice
    assert "algemene modelantwoord" in result.answer


def test_library_filter_prevents_cross_library_results(tmp_path: Path) -> None:
    service, _ = build_service(tmp_path)
    service.create_library("SQL")
    service.create_library("WOII")
    sql_source = tmp_path / "query.sql"
    sql_source.write_text("select kalender from datum_dimensie;", encoding="utf-8")
    war_source = tmp_path / "woii.md"
    war_source.write_text("Historische notitie zonder zoekterm.", encoding="utf-8")
    service.ingest("sql", sql_source)
    service.ingest("woii", war_source)

    results = service.search("kalender", ["sql"])

    assert results
    assert {result.library_slug for result in results} == {"sql"}


def test_exact_identifier_match_survives_unhelpful_semantic_embedding(
    tmp_path: Path,
) -> None:
    service, _ = build_service(tmp_path)
    service.create_library("SQL")
    exact_source = tmp_path / "procedure.md"
    exact_source.write_text(
        "De procedure gebruikt NO_PRICING_RECORD_FOR_KEY voor deze fout.",
        encoding="utf-8",
    )
    distractor = tmp_path / "other.md"
    distractor.write_text(
        "Algemene tekst zonder het gevraagde object.", encoding="utf-8"
    )
    service.ingest("sql", tmp_path)

    results = service.search("NO_PRICING_RECORD_FOR_KEY", ["sql"], top_k=2)

    assert results[0].source_name == "procedure.md"
