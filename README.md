# trv-webcams

Webbapplikation som sparar bilder från Trafikverkets webbkameror och skapar timelapses (MP4) och GIF-animationer, med GPU-acceleration via ffmpeg (primärt NVIDIA NVENC).

- **Karta och lista** över alla ca 1 600 kameror i Sverige, med sökning och filter per län.
- **Du väljer själv vilka kameror som är aktiva.** Bara aktiva kameror sparar bilder och får timelapses, så att du inte lagrar hela Sveriges kameranät. Du kan också aktivera eller avaktivera alla kameror i ett län på en gång.
- **Nattlig körning** (02:00) som skapar timelapses för **1, 7, 14 och 31 dygn, ett kvartal och ett år**, både som MP4 och GIF.
- Knappen **"Generera timelapse"** på varje kamerasida skapar en timelapse direkt.
- **Graf med vägtemperatur** i timelapses och GIF:ar. Grafen hämtar data från närmaste vägväderstation inom 20 km och ritas fram bild för bild. Den är påslagen som standard och kan stängas av per kamera.
- **Retention:** alla bilder sparas i 90 dagar. Den första bilden varje timme sparas i ett glest arkiv i 365 dagar, så att det går att göra årsklippet.
- Webbgränssnittet lyssnar på port **8933** och är på svenska.

## Snabbstart

```bash
cp .env.example .env        # fyll i TRV_API_KEY
docker compose up -d
```

Öppna http://localhost:8933, sök upp de kameror du vill följa och slå på dem.

Du kan också köra den färdiga imagen direkt:

```bash
docker run -d --name trv-webcams --gpus all -p 8933:8933 \
  -e TRV_API_KEY=... -v $(pwd)/data:/data ghcr.io/tubalainen/trv-webcams:latest
```

> **Hemligheter:** `TRV_API_KEY` sätts bara via miljövariabel eller en lokal `.env`-fil. `.env` står i `.gitignore` och får aldrig committas. API-nyckel skaffar du på https://api.trafikinfo.trafikverket.se/.

## GPU-acceleration

| Läge | Kodare | Krav |
|------|--------|------|
| `nvidia` | `h264_nvenc` | NVIDIA-drivrutin och [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/) på värden |
| `vaapi` | `h264_vaapi` | `/dev/dri` mappad in i containern (Intel/AMD) |
| `qsv` | `h264_qsv` | Intel Quick Sync |
| `cpu` | `libx264` | – |

Med `HWACCEL=auto` (standard) provar appen vid start kodarna i ordningen NVIDIA → VAAPI → QSV → CPU och väljer den första som fungerar. Resultatet skrivs i containerns logg (`docker compose logs trv-webcams`): vilket NVIDIA-kort som syns (via `nvidia-smi`), om varje kodare fungerade eller varför den inte gjorde det, och vilken kodare som används:

```
INFO app.ffmpeg: Hardware acceleration check (HWACCEL=auto)
INFO app.ffmpeg:   NVIDIA GPU found: NVIDIA GeForce RTX 3060 (driver 550.120, 12288 MiB)
INFO app.ffmpeg:   nvidia h264_nvenc  OK
INFO app.ffmpeg: Using video encoder h264_nvenc (GPU)
```

Om ingen GPU syns i containern loggas en varning med tips om NVIDIA Container Toolkit och `deploy`-blocket i compose-filen. Om en GPU-kodning misslyckas under körning gör appen om den på CPU. Statusraden i gränssnittet och `/api/status` visar vilken kodare som används. GIF-filer skapas alltid på CPU med `palettegen`/`paletteuse`, eftersom det inte finns någon GPU-kodare för GIF.

Imagen bygger på Debians ffmpeg-paket, som har stöd för NVENC/NVDEC, VAAPI och QSV. NVIDIA-biblioteken laddas från värdens drivrutin när containern körs.

## Lagring

Med normal bildupplösning (cirka 27 kB per bild) och en bild var 5:e minut tar varje aktiv kamera ungefär:

- ~7,7 MB per dygn
- ~0,7 GB för 90 dagars bilder, plus ~0,25 GB för årsarkivet

Statusraden visar hur mycket som används nu och en uppskattning för när retentionen är full.

```
/data/app.db
/data/snapshots/{kamera}/{ÅÅÅÅ-MM-DD}/{HHMMSS}.jpg   90 dagar
/data/archive/{kamera}/{ÅÅÅÅ-MM-DD}/{HH}.jpg         365 dagar (hårdlänk, 1 bild/timme)
/data/timelapses/{kamera}/{period}.mp4|.gif         senaste versionen per period
```

Containern startar som root, sätter ägare på `/data` till `PUID:PGID` (standard `1000:1000`) och kör sedan appen som den användaren. En `./data`-mapp som Docker har skapat som root fungerar därför direkt. Ägaren ändras bara när något i `/data` har fel ägare. Kör du containern med `user:` i compose eller `--user` hoppas det steget över, och då måste mappen redan vara skrivbar för den användaren.

## Vägtemperatur i timelapses

Nere till höger i varje timelapse och GIF visas en liten graf över **vägytans temperatur**:
- X-axeln täcker hela klippets period, och kurvan ritas fram till den aktuella bildens tidpunkt.
- Det aktuella värdet visas blått vid 0 °C och kallare, annars orange.
- En streckad linje markerar 0 °C, och längst ner står periodens lägsta och högsta värde.

- **Datakälla:** Trafikverkets vägväderstationer (`WeatherMeasurepoint`/`WeatherObservation`). Varje kamera kopplas till närmaste station *som mäter vägytans temperatur* inom `TEMP_STATION_MAX_KM` (standard 20 km). Stationen och det senaste värdet visas på kamerasidan.
- **Historik:** Trafikverket sparar bara cirka 7 dygns mätningar. Appen hämtar därför vägtemperaturen var 15:e minut för stationerna som hör till aktiva kameror och sparar den i 365 dagar, lika länge som årsarkivet. När en kamera aktiveras hämtas de senaste 7 dygnen direkt. Grafer för längre perioder fylls på med tiden, och det som saknas lämnas tomt.
- **Av och på:** grafen är påslagen som standard i nattkörningen. Under *Generera timelapse* på kamerasidan kan den stängas av för just den kameran.
- **Upplösning:** Trafikverkets normala bilder är bara cirka 384 pixlar breda. Videon skalas därför upp till minst 768 pixlars bredd, så att grafens text går att läsa.

## Konfiguration

Alla inställningar finns i [`.env.example`](.env.example). De viktigaste:

| Variabel | Standard | Beskrivning |
|----------|----------|-------------|
| `TRV_API_KEY` | – | **Krävs.** Trafikverkets API-nyckel |
| `HOST_PORT` | `8933` | Port på värden (docker compose) |
| `COLLECT_INTERVAL_MINUTES` | `5` | Hur ofta bilder hämtas |
| `TRV_FULLSIZE` | `false` | Hämta bilder i full storlek (cirka 4 gånger större) |
| `SNAPSHOT_RETENTION_DAYS` | `90` | Hur länge alla bilder sparas |
| `ARCHIVE_RETENTION_DAYS` | `365` | Hur länge timarkivet sparas |
| `NIGHTLY_HOUR` / `NIGHTLY_MINUTE` | `2` / `0` | När nattkörningen startar |
| `HWACCEL` | `auto` | `auto`, `nvidia`, `vaapi`, `qsv` eller `cpu` |
| `MAX_CONCURRENT_JOBS` | `2` | Antal timelapse-jobb som körs samtidigt |
| `TEMP_STATION_MAX_KM` | `20` | Största avstånd till en vägväderstation för temperaturgrafen |
| `TEMP_COLLECT_INTERVAL_MINUTES` | `15` | Hur ofta vägtemperatur hämtas |
| `DEFAULT_ENABLED_CAMERAS` | – | Kamera-id:n som aktiveras vid första start |
| `PUID` / `PGID` | `1000` / `1000` | Användare och grupp som appen körs som i containern |

## API

| Metod | Sökväg | Beskrivning |
|-------|--------|-------------|
| GET | `/api/cameras?q=&county=&enabled=` | Lista kameror |
| GET | `/api/cameras/{id}` | Detaljer och timelapses för en kamera |
| PATCH | `/api/cameras/{id}` `{"enabled": true}` | Aktivera eller avaktivera en kamera |
| POST | `/api/cameras/bulk` `{"county": 14, "enabled": true}` | Aktivera eller avaktivera alla kameror i ett län, eller en lista med `ids` |
| POST | `/api/cameras/{id}/timelapses` `{"periods": ["1d","7d"]}` | Skapa timelapse (svarar med ett jobb) |
| GET | `/api/jobs/{id}` | Status för ett jobb |
| GET | `/api/status`, `/api/health` | Status och hälsokontroll |

## Utveckling

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
DATA_DIR=./data python -m app.main
```

## CI/CD och säkerhet

- **CI** (`.github/workflows/ci.yml`) kör ruff, pytest (inklusive riktig ffmpeg-kodning), pip-audit, bygger Docker-imagen och gör ett röktest.
- **Release** (`.github/workflows/release.yml`) bygger imagen för amd64 och arm64 och publicerar den på `ghcr.io/tubalainen/trv-webcams`:
  - en push till `main` ger taggarna `edge` och `sha-…`
  - en release ger taggarna `X.Y.Z`, `X.Y` och `latest`

  En release görs på något av dessa sätt:
  - **Höj versionen** i `app/__init__.py` (och `pyproject.toml`) och merga till `main`. Om taggen `vX.Y.Z` inte finns bygger workflowet imagen, skapar taggen och en GitHub-release med genererade release notes.
  - **Actions → Release image → Run workflow** på `main`, med versionen (t.ex. `0.2.0`) som input. Workflowet bygger och publicerar imagen och skapar sedan taggen `vX.Y.Z` och en GitHub-release med genererade release notes.
  - Pusha en tagg; GitHub-releasen skapas då automatiskt om den saknas:

    ```bash
    git tag v0.2.0 && git push origin v0.2.0
    ```
- **Säkerhetsskanning** (`.github/workflows/security.yml`) körs varje dag och på begäran:
  - Trivy skannar den aktuella imagen och den publicerade `:latest`-imagen
  - Trivy skannar även repot efter sårbara beroenden, hemligheter och felkonfiguration
  - pip-audit kontrollerar Python-beroendena

  Resultaten hamnar under *Security → Code scanning*. Körningen misslyckas om det finns kritiska eller allvarliga sårbarheter som har en åtgärd.
- **Dependabot** uppdaterar pip-paket, GitHub Actions och Docker-basimagen varje vecka.

Bilddata: © Trafikverket. Kartdata: © OpenStreetMap-bidragsgivare.
