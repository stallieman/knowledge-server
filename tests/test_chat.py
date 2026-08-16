from pathlib import Path

from knowledge_server.chat import ChatStore


def test_chat_history_and_markdown_export(tmp_path: Path) -> None:
    store = ChatStore(tmp_path / "chat.db")
    conversation = store.create_conversation()
    store.add_message(conversation.id, "user", "Wat is RAG?")
    store.add_message(
        conversation.id,
        "assistant",
        "Een zoekgestuurd antwoord.",
        [{
            "source_name": "rag.md",
            "library_slug": "ai-engineering",
            "start_line": 1,
            "end_line": 4,
        }],
    )

    assert store.list_conversations()[0].title == "Wat is RAG?"
    assert len(store.messages(conversation.id)) == 2
    exported = store.export_markdown(conversation.id)
    assert "# Wat is RAG?" in exported
    assert "rag.md" in exported
