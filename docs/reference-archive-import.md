# Gemengd naslagarchief importeren

`scripts/import_reference_archive.py` verwerkt het aangeleverde `Archief.zip`
herhaalbaar en lokaal. De importeur controleert ZIP-paden, detecteert exacte
dubbelen, scant doorzoekbare inhoud op waarschijnlijke secrets en routeert ieder
bestand naar een bibliotheek.

De import van 2026-08-09 gebruikt deze domeinen:

- Linux: Linux-commando's, overstapnotities, tmux en Neovim;
- Softwareontwikkeling: VS Code, Git, Python en PowerShell;
- DevOps & Cloud: Docker;
- Elastic Stack: Elasticsearch en Metricbeat;
- SQL: functies, windowfuncties en tabel-/updatevoorbeelden;
- WBIH: WBIH, TDV en de Azure DevOps-wikiblauwdruk;
- Power BI: semantic models, Power Query/ETL, DAX en rapportontwerp;
- AI Engineering: ECE/TDV/chatbot-architectuur;
- Project: TDV View Exporter: alleen de leesbare broncode en documentatie uit de
  geneste project-ZIP.

De dubbele wikiblauwdruk wordt slechts eenmaal geplaatst. Binaire JARs,
macOS-metadata en lege `.gitkeep`-bestanden uit het geneste project worden niet
geïndexeerd.

```fish
uv run python scripts/import_reference_archive.py ~/Downloads/Archief.zip
uv run knowledge-server init
uv run knowledge-server library create "Project: TDV View Exporter" \
  --slug project-tdv-view-exporter
uv run knowledge-server ingest-all
```

De originele ZIP blijft ongewijzigd. Het JSON-manifest in `data/import` legt per
bron hash, bestemming, bibliotheek en importstatus vast en wordt zelf niet door
`ingest-all` meegenomen.
