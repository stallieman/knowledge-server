# GitHub-projecten als lokale RAG-bron

De lokale LLM kan GitHub-projecten gebruiken zonder broncode naar een externe
AI-dienst te sturen. GitHub of `git` verzorgt alleen de synchronisatie; parsing,
embeddings, zoeken en antwoorden blijven lokaal via Ollama en SQLite.

## Bibliotheekgrens

Maak standaard één bibliotheek per project, bijvoorbeeld:

```fish
uv run knowledge-server library create "Project Home Energy Analytics" \
  --slug project-home-energy-analytics
```

Dat voorkomt dat gelijknamige bestanden, helpers en configuraties uit verschillende
repositories elkaar verdringen. Bij een projectoverstijgende vraag kunnen meerdere
projectbibliotheken bewust tegelijk worden geselecteerd.

## Veilige lokale snapshotimport

```fish
uv run knowledge-server ingest-git project-home-energy-analytics \
  ~/projects/home-energy-analytics
```

`ingest-git` gebruikt `git ls-files` en indexeert dus alleen door Git bijgehouden
bestanden. Genegeerde en ongetrackte bestanden worden niet gelezen. Ook bekende
secretbestanden en sleutelformaten, dependency-/buildmappen, lockbestanden en bestanden
groter dan 2 MB worden uitgesloten. Verwijderde Git-bestanden worden bij een volgende
run uit de index opgeschoond.

Ondersteunde codeformaten omvatten onder meer Python, SQL, YAML, JSON, TOML, shell,
PowerShell, JavaScript/TypeScript, C#, Java, Go, Rust, Dockerfile en Makefile. Het
relatieve repositorypad wordt als zoekmetadata opgeslagen, zodat een vraag op
bijvoorbeeld een helpernaam niet alleen `utils.py` maar `src/import/utils.py` ziet.

Periodieke lokale projectimport wordt geconfigureerd in
`config/git-sources.json`. De bestaande zuinige index-timer start `ingest-all`, dat
eerst de documentbibliotheken en daarna iedere geconfigureerde Git-checkout
incrementeel verwerkt. Ongewijzigde bestanden krijgen geen nieuwe embeddings.

## GitHub-synchronisatie

Voor privé-repositories is de aanbevolen route:

1. authenticatie lokaal met GitHub CLI of SSH;
2. een aparte checkout/mirror onder een lokale datamap;
3. alleen de default branch met `git pull --ff-only` bijwerken;
4. na een succesvolle update `ingest-git` uitvoeren;
5. de sync als user-service en timer laten draaien.

Gebruik read-only toegang tot repository contents waar mogelijk. De lokale
kennisserver heeft geen write-, issue- of PR-rechten nodig om code te indexeren.
Automatische force-resets zijn niet gewenst; een niet-fast-forward update moet
stoppen en zichtbaar worden gelogd.

Issues, pull requests, releases en wiki's zijn andere bronsoorten dan de code zelf.
Die kunnen later als afzonderlijke Markdown-snapshots worden toegevoegd, met
repository, nummer, status, auteur, labels en update-tijd als metadata. Daardoor kan
de assistent onderscheid maken tussen huidig gedrag in code, voorgenomen wijzigingen
in een PR en historische discussie in een gesloten issue.

## Huidige lokale situatie

Op 2026-08-09 zijn lokaal `knowledge-server` en `local-meeting-transcriber`
gevonden. Beide hebben nog geen geconfigureerde Git-remote. Ze kunnen al met
`ingest-git` worden geïndexeerd; GitHub-sync kan pas gericht worden geactiveerd nadat
de gewenste `owner/repository`-namen of remote-URL's bekend zijn.
