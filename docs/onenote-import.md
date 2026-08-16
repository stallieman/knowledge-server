# OneNote-back-up importeren

Een OneNote-back-up uit OneDrive bevat binaire `.one`-secties. De lokale import
gebruikt daarom twee strikt gescheiden stappen:

1. `one2html` converteert iedere `.one`-sectie naar HTML;
2. `scripts/import_onenote_html.py` maakt daar schone Markdown-bronnen van.

De oorspronkelijke ZIP blijft ongewijzigd. De OneNote-prullenbak wordt niet
geïmporteerd. Het importschema maakt één Markdownbestand per echte pagina, voegt
herkomst-, sectie-, onderwerp- en privacy-metadata toe en maakt per bibliotheek een
kenniskaart. Pagina's zonder inhoud worden overgeslagen.

## Bibliotheekroutering

| OneNote-sectie | Bibliotheek |
| --- | --- |
| Algemeen, ILM, Installatie, Kibana DevOps, Logstash, Troubleshooting | Elastic Stack |
| Ansible, Azure, Docker, Kubernetes | DevOps & Cloud |
| Commando's handig, Omarchy, VIM Motions | Linux |
| Dev Environment, Development, GIT, MAC, Python | Softwareontwikkeling |
| OSINT, Security | Security & OSINT |
| SQL | SQL |
| TDV | WBIH |
| Gerechten | Persoonlijk |

## Privacycontrole

De importeur verwijdert privésleutelblokken, Authorization-waarden, Bearer-tokens,
gevoelige URL-queryparameters en waarschijnlijk ingevulde wachtwoord-, token- of
API-sleutelwaarden. Omgevingsnamen en technische hostcontext blijven behouden,
omdat die vaak nodig zijn om operationele vragen te beantwoorden. Alle documenten
worden gemarkeerd als `tailnet-private`.

Na conversie:

```fish
uv run python scripts/import_onenote_html.py /pad/naar/one2html-uitvoer
uv run knowledge-server init
uv run knowledge-server ingest-root data/import
```

Het manifest `data/import/onenote-import-manifest.json` registreert per pagina de
bestemming, onderwerpen, status en het aantal redactions. Het manifest zelf wordt
niet geïndexeerd, omdat JSON geen ondersteund documentformaat is.
