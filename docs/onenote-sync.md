# OneNote naar kennisbibliotheken

## Doelindeling

Gebruik bij voorkeur OneNote-secties die overeenkomen met de bibliotheken:

| OneNote-sectie | Bibliotheekslug |
|---|---|
| WBIH | `wbih` |
| SQL | `sql` |
| Linux | `linux` |
| Home Energy | `home-energy` |
| WOII | `woii` |
| Persoonlijke video's | `persoonlijke-videos` |
| ChatGPT-archief | `chatgpt-archief` |

Een pagina hoort bij precies één primaire bibliotheek. Als dezelfde pagina echt in
meerdere bibliotheken nodig is, kan de toekomstige sync expliciete tags gebruiken.

## Route die nu werkt

1. Exporteer een OneNote-pagina als PDF, Word of HTML.
2. Plaats het bestand in `data/import/<bibliotheekslug>/`.
3. De systemd-timer verwerkt nieuwe en gewijzigde bestanden binnen dertig minuten.

Een handmatige import kan met:

```fish
uv run knowledge-server ingest-root data/import
```

## Waarom de deellink niet de sync-interface is

Een OneDrive/OneNote-deellink is bedoeld voor interactief gebruik in een browser.
De achterliggende webpagina en tijdelijke redirects zijn geen stabiele document-API.
De link kan ook bredere toegang geven dan wenselijk wanneer hij wordt doorgestuurd.

## Geplande automatische Graph-sync

De structurele koppeling gebruikt Microsoft Graph:

1. interactieve device-code-login met het Microsoft-account van de notebook;
2. alleen-lezen, gedelegeerde `Notes.Read`-toegang;
3. notebooks, secties en pagina's ophalen via `/me/onenote/...`;
4. pagina-inhoud als HTML ophalen en lokaal normaliseren;
5. OneNote-sectie via bovenstaande tabel naar één bibliotheek mappen;
6. alleen gewijzigde pagina's opnieuw indexeren;
7. OAuth-token versleuteld lokaal opslaan en nooit in Git plaatsen.

Microsoft Graph OneNote ondersteunt geen app-only-authenticatie. De eigenaar moet de
eerste koppeling daarom interactief goedkeuren; daarna kan de lokale server met een
geldige gedelegeerde sessie periodiek synchroniseren.

De SharePoint-plugin van Codex is niet de runtime voor deze sync. Zo'n connector kan
Codex toegang geven tijdens een taak, maar levert niet automatisch authenticatie aan
de lokale, permanent draaiende knowledge server.
