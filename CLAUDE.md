# CLAUDE.md

Instruktioner för Claude när du arbetar i det här repot. Läs också projektminnet:

@docs/project-memory.md

## Projektet

trv-webcams hämtar bilder från Trafikverkets webbkameror, sparar dem och gör timelapses
(MP4 med GPU-kodning och GIF) per kamera. Det är en FastAPI-app med ett statiskt
gränssnitt och körs som en Docker-container.

| Del | Fil |
|-----|-----|
| Start, livscykel | `app/main.py` |
| Inställningar (env) | `app/config.py` |
| REST-API och mediafiler | `app/api/routes.py` |
| Hämtning av bilder från Trafikverket | `app/trv_client.py`, `app/collector.py` |
| Filstruktur under `/data` | `app/storage.py` |
| Timelapse-jobb, encoder | `app/timelapse.py` |
| ffmpeg-kommandon, GPU-detektering | `app/ffmpeg.py` |
| Schemaläggning, retention | `app/scheduler.py`, `app/retention.py` |
| Gränssnitt | `app/static/` (vanlig JS, inget byggsteg) |
| Container | `Dockerfile`, `docker-entrypoint.sh`, `docker-compose.yml` |

## Arbetsflöde: varje ändring ska kunna spåras

1. **Issue först.** När användaren beskriver en förbättring eller bugg skapar du ett
   GitHub-issue i `tubalainen/trv-webcams` innan du börjar koda. Märk det med `bug` eller
   `enhancement`, och skriv problemet och förslaget på lösning.
2. **Gren och commits.** Arbeta på den gren sessionen anvisar. Skriv commit-meddelanden
   på engelska och hänvisa till issuet (`Refs #N`).
3. **PR.** PR-beskrivningen skrivs på svenska och innehåller `Closes #N` för varje issue
   den löser, så att issuet stängs vid merge. Ta med vad som är testat och vad som inte är det.
4. **Release.** Höj `__version__` i `app/__init__.py` och `version` i `pyproject.toml`
   i PR:en. En merge till main med en version som saknar tagg publicerar automatiskt
   `:X.Y.Z`, `:X.Y`, `:latest`, taggen `vX.Y.Z` och en GitHub-release
   (`.github/workflows/release.yml`). Merga bara när användaren har bett om det och CI är grönt.
5. **Projektminne.** Uppdatera `docs/project-memory.md` i samma PR: ändringslogg,
   beslut och sådant som är bra att veta.

## Hemligheter – får aldrig pushas

- API-nycklar, tokens, lösenord och andra hemligheter får **aldrig** committas eller
  pushas, och får inte stå i issues, PR:er eller kommentarer. Det gäller även nycklar
  som användaren klistrar in i chatten.
- Hemligheter ligger bara i `.env` (gitignorerad) eller i miljövariabler. `.env.example`
  innehåller bara platshållare.
- Kör `scripts/check-secrets.sh` innan varje push. CI kör samma kontroll och fallerar om
  något ser ut som en hemlighet.
- Om en hemlighet ändå hamnar i en commit: pusha inte, ta bort den ur historiken och
  be användaren att byta nyckeln.

## Kontroller före push

```bash
scripts/check-secrets.sh
ruff check . && ruff format --check .
pytest -q
```

Ändringar i gränssnittet (`app/static/`) ska provas i en webbläsare (Playwright) mot
en lokalt startad app:
`DATA_DIR=<tmp> SCHEDULER_ENABLED=false PORT=8955 python -m app.main`.

## Konventioner

- Gränssnittet, README och PR-texter är på svenska. Kod, kommentarer, loggar och
  commit-meddelanden är på engelska.
- Python 3.15, ruff med radlängd 110. Inga nya beroenden utan anledning, och gränssnittet
  har inget byggsteg.
- Appen kör som uid/gid `PUID`/`PGID` (1000). Startskriptet rättar ägaren på `/data`.
- Sökvägar som byggs av indata från användaren valideras med regex och `storage.safe_id`.
