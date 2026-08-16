#!/usr/bin/env python3
"""Convert one2html output into private, searchable Markdown documents."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

SECTION_TO_LIBRARY = {
    "Algemeen": "elastic-stack",
    "ILM": "elastic-stack",
    "Installatie": "elastic-stack",
    "Kibana DevOps": "elastic-stack",
    "Logstash": "elastic-stack",
    "Troubleshooting": "elastic-stack",
    "Ansible": "devops-cloud",
    "Azure": "devops-cloud",
    "Docker": "devops-cloud",
    "Kubernetes": "devops-cloud",
    "Commando's handig": "linux",
    "Omarchy": "linux",
    "VIM Motions": "linux",
    "Dev Environment": "software-development",
    "Development": "software-development",
    "GIT": "software-development",
    "MAC": "software-development",
    "Python": "software-development",
    "OSINT": "security-osint",
    "Security": "security-osint",
    "SQL": "sql",
    "TDV": "wbih",
    "Gerechten": "persoonlijk",
}

LIBRARY_NAMES = {
    "elastic-stack": "Elastic Stack",
    "devops-cloud": "DevOps & Cloud",
    "linux": "Linux",
    "software-development": "Softwareontwikkeling",
    "security-osint": "Security & OSINT",
    "sql": "SQL",
    "wbih": "WBIH",
    "persoonlijk": "Persoonlijk",
}

TOPIC_RULES = {
    "ansible": ("ansible", "playbook", "inventory"),
    "azure": ("azure", "dp-900"),
    "containers": ("container", "docker", "podman"),
    "docker-compose": ("docker compose", "docker-compose", "compose.yaml"),
    "elastic-ilm": ("ilm", "index lifecycle", "data stream", "rollover"),
    "elasticsearch": ("elasticsearch", "elastic", "index template", "shard"),
    "git": ("git ", "git-", "branch", "merge", "commit", "rebase"),
    "kibana": ("kibana", "dev tools", "kql"),
    "kubernetes": ("kubernetes", "kubectl", "k8s", "deployment", "pod "),
    "linux-cli": ("linux", "shell", "bash", "systemctl", "journalctl"),
    "logstash": ("logstash", "pipeline", "grok", "filter "),
    "neovim": ("neovim", "nvim", "vim motion"),
    "networking": ("network", "poort", " port ", "dns", "tcp", "udp"),
    "omarchy": ("omarchy", "hyprland"),
    "osint": ("osint", "shodan", "censys", "sherlock", "theharvester"),
    "postgresql": ("postgres", "postgresql", "psql"),
    "python": ("python", "pandas", "pyspark", "virtual environment"),
    "security": ("security", "ssh", "ssl", "tls", "certificate"),
    "sql": ("sql", "select ", "join ", "stored procedure", "cte"),
    "tdv": ("tdv", "tibco data virtualization", "chocoinstallations"),
}

PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
    re.DOTALL,
)
AUTHORIZATION_RE = re.compile(r"(?im)^(\s*authorization\s*:\s*)(?:basic|bearer)\s+\S+")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|client_secret|sig|token)=)[^&#\s]+"
)
CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|api[_-]?key|client[_-]?secret|"
    r"access[_-]?token)\b\s*(?::|=|=>)\s*[\"']?)"
    r"([A-Za-z0-9._~+/=-]{16,})([\"']?)"
)
COMMAND_SECRET_RE = re.compile(
    r"(?i)(--(?:password|passwd|api-key|client-secret|access-token)\s+)"
    r"(?![$<{%[])(\S+)"
)


@dataclass(frozen=True)
class ImportRecord:
    source: str
    target: str | None
    library: str | None
    section: str
    title: str
    topics: list[str]
    characters: int
    redactions: int
    status: str


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return slug or "pagina"


def yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _paragraph_markdown(paragraph: object) -> str:
    fragment = BeautifulSoup(str(paragraph), "html.parser")
    for link in fragment.find_all("a"):
        label = " ".join(link.get_text(" ", strip=True).split())
        href = str(link.get("href", "")).strip()
        if href and href not in label:
            link.replace_with(f"[{label or href}]({href})")
    raw_text = fragment.get_text(" ", strip=False).replace("\u00a0", " ")
    indentation = len(raw_text) - len(raw_text.lstrip(" "))
    normalized = " ".join(raw_text.strip().split())
    return (" " * min(indentation, 16)) + normalized


def extract_page(path: Path) -> tuple[str, list[str]]:
    soup = BeautifulSoup(
        path.read_text(encoding="utf-8", errors="replace"), "html.parser"
    )
    title = soup.title.get_text(" ", strip=True) if soup.title else path.stem
    lines: list[str] = []
    for paragraph in soup.select("body p"):
        if paragraph.find_parent(class_="title") is not None:
            continue
        line = _paragraph_markdown(paragraph).replace("\u00a0", " ").rstrip()
        if line.strip() and (not lines or line != lines[-1]):
            lines.append(line)
    return title.strip() or path.stem, lines


def redact_secrets(text: str) -> tuple[str, int]:
    redactions = 0

    def replace(pattern: re.Pattern[str], replacement: str, value: str) -> str:
        nonlocal redactions
        value, count = pattern.subn(replacement, value)
        redactions += count
        return value

    text = replace(PRIVATE_KEY_RE, "[VERWIJDERDE PRIVÉSLEUTEL]", text)
    text = replace(
        AUTHORIZATION_RE,
        r"\1[VERWIJDERDE AUTHORIZATION-WAARDE]",
        text,
    )
    text = replace(BEARER_RE, "Bearer [VERWIJDERD TOKEN]", text)
    text = replace(URL_SECRET_RE, r"\1[VERWIJDERD]", text)
    text = replace(CREDENTIAL_VALUE_RE, r"\1[VERWIJDERD]\3", text)
    text = replace(COMMAND_SECRET_RE, r"\1[VERWIJDERD]", text)
    return text, redactions


def infer_topics(section: str, title: str, content: str) -> list[str]:
    haystack = f" {section} {title} {content} ".lower()
    topics = [
        topic
        for topic, needles in TOPIC_RULES.items()
        if any(needle in haystack for needle in needles)
    ]
    return topics[:12]


def page_markdown(
    *,
    title: str,
    section: str,
    library: str,
    topics: list[str],
    source: str,
    content: str,
) -> str:
    topic_lines = "\n".join(f"  - {yaml_string(topic)}" for topic in topics)
    if not topic_lines:
        topic_lines = '  - "overig"'
    return (
        "---\n"
        f"title: {yaml_string(title)}\n"
        'source_type: "onenote-export"\n'
        'source_owner: "privé"\n'
        'visibility: "tailnet-private"\n'
        f"source_archive: {yaml_string(source)}\n"
        f"onenote_section: {yaml_string(section)}\n"
        f"library: {yaml_string(library)}\n"
        "topics:\n"
        f"{topic_lines}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"> Herkomst: persoonlijke OneNote-notitie, sectie **{section}**. "
        "De inhoud kan verouderde commando's of omgevingsspecifieke "
        "waarden bevatten.\n\n"
        f"{content.strip()}\n"
    )


def write_knowledge_maps(
    records: list[ImportRecord], output_root: Path, source_name: str
) -> None:
    by_library: dict[str, list[ImportRecord]] = defaultdict(list)
    for record in records:
        if record.status == "imported" and record.library:
            by_library[record.library].append(record)

    for library, items in sorted(by_library.items()):
        sections = sorted({item.section for item in items})
        topic_counts = Counter(topic for item in items for topic in item.topics)
        lines = [
            "---",
            f'title: "Kenniskaart OneNote — {LIBRARY_NAMES[library]}"',
            'source_type: "derived-routing-map"',
            'source_owner: "privé"',
            'visibility: "tailnet-private"',
            f"source_archive: {yaml_string(source_name)}",
            f"library: {yaml_string(library)}",
            "---",
            "",
            f"# Kenniskaart OneNote — {LIBRARY_NAMES[library]}",
            "",
            "Deze kaart routeert zoekvragen naar de oorspronkelijke "
            "persoonlijke notities. "
            "Hij is geen vervanging voor die bronnen en voegt geen nieuwe feiten toe.",
            "",
            "## Dekking",
            "",
            f"- Pagina's: {len(items)}",
            f"- OneNote-secties: {', '.join(sections)}",
            "- Belangrijkste onderwerpen: "
            + (
                ", ".join(topic for topic, _ in topic_counts.most_common(12))
                or "overig"
            ),
            "",
            "## Bronnen per sectie",
            "",
        ]
        for section in sections:
            lines.append(f"### {section}")
            lines.append("")
            for item in sorted(
                (candidate for candidate in items if candidate.section == section),
                key=lambda candidate: candidate.title.casefold(),
            ):
                topics = (
                    f" — onderwerpen: {', '.join(item.topics)}" if item.topics else ""
                )
                lines.append(f"- {item.title}{topics}")
            lines.append("")

        target = output_root / library / "onenote" / "00-kenniskaart-onenote.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def import_pages(
    html_root: Path, output_root: Path, source_name: str
) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for path in sorted(html_root.glob("*/*.html")):
        section = path.parent.name
        title, lines = extract_page(path)
        content = "\n".join(lines).strip()
        library = SECTION_TO_LIBRARY.get(section)

        if library is None:
            records.append(
                ImportRecord(
                    source=str(path.relative_to(html_root)),
                    target=None,
                    library=None,
                    section=section,
                    title=title,
                    topics=[],
                    characters=len(content),
                    redactions=0,
                    status="unmapped",
                )
            )
            continue

        if len(content) < 100:
            records.append(
                ImportRecord(
                    source=str(path.relative_to(html_root)),
                    target=None,
                    library=library,
                    section=section,
                    title=title,
                    topics=[],
                    characters=len(content),
                    redactions=0,
                    status="skipped-empty",
                )
            )
            continue

        fence_count = sum(
            line.lstrip().startswith("```") for line in content.splitlines()
        )
        if fence_count % 2:
            content = f"{content}\n```"
        content, redactions = redact_secrets(content)
        topics = infer_topics(section, title, content)
        target = (
            output_root
            / library
            / "onenote"
            / slugify(section)
            / f"{slugify(title)}.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            page_markdown(
                title=title,
                section=section,
                library=library,
                topics=topics,
                source=source_name,
                content=content,
            ),
            encoding="utf-8",
        )
        records.append(
            ImportRecord(
                source=str(path.relative_to(html_root)),
                target=str(target),
                library=library,
                section=section,
                title=title,
                topics=topics,
                characters=len(content),
                redactions=redactions,
                status="imported",
            )
        )

    write_knowledge_maps(records, output_root, source_name)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_root", type=Path, help="Directory produced by one2html")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/import"),
        help="Knowledge-server import root",
    )
    parser.add_argument(
        "--source-name",
        default="OneNote-backup.zip",
        help="Stable archive name stored in document metadata",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/import/onenote-import-manifest.json"),
    )
    args = parser.parse_args()

    html_root = args.html_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    if not html_root.is_dir():
        raise SystemExit(f"HTML root bestaat niet: {html_root}")

    records = import_pages(html_root, output_root, args.source_name)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_archive": args.source_name,
        "records": [asdict(record) for record in records],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    counts = Counter(record.status for record in records)
    redactions = sum(record.redactions for record in records)
    print(
        f"OneNote import klaar: imported={counts['imported']} "
        f"skipped-empty={counts['skipped-empty']} "
        f"unmapped={counts['unmapped']} redactions={redactions}"
    )
    print(f"Manifest: {args.manifest.resolve()}")


if __name__ == "__main__":
    main()
