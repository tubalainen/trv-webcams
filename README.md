# trv-webcams

Webbapplikation som sparar bilder från Trafikverkets webbkameror och skapar timelapses (MP4) och GIF-animationer, med GPU-acceleration via ffmpeg (primärt NVIDIA NVENC).

- **Karta och lista** över alla ca 1 600 kameror i Sverige, med sökning och filter per län.
- **Du väljer själv vilka kameror som är aktiva.** Bara aktiva kameror sparar bilder och får timelapses, så att du inte lagrar hela Sveriges kameranät. Du kan också aktivera eller avaktivera alla kameror i ett län på en gång.
- **Nattlig körning** (02:00) som skapar timelapses för **1, 7, 14 och 31 dygn, ett kvartal och ett år**, både som MP4 och GIF.
- Knappen **"Generera timelapse"** på varje kamerasida skapar en timelapse direkt.
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

Med `HWACCEL=auto` (standard) provar appen vid start kodarna i ordningen NVIDIA → VAAPI → QSV → CPU och väljer den första som fungerar. Om en GPU-kodning misslyckas under körning gör appen om den på CPU. Statusraden i gränssnittet och `/api/status` visar vilken kodare som används. GIF-filer skapas alltid på CPU med `palettegen`/`paletteuse`, eftersom det inte finns någon GPU-kodare för GIF.

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
| `DEFAULT_ENABLED_CAMERAS` | – | Kamera-id:n som aktiveras vid första start |

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
  - en tagg `vX.Y.Z` ger taggarna `X.Y.Z`, `X.Y` och `latest`

  ```bash
  git tag v0.1.0 && git push origin v0.1.0
  ```
- **Säkerhetsskanning** (`.github/workflows/security.yml`) körs varje dag och på begäran:
  - Trivy skannar den aktuella imagen och den publicerade `:latest`-imagen
  - Trivy skannar även repot efter sårbara beroenden, hemligheter och felkonfiguration
  - pip-audit kontrollerar Python-beroendena

  Resultaten hamnar under *Security → Code scanning*. Körningen misslyckas om det finns kritiska eller allvarliga sårbarheter som har en åtgärd.
- **Dependabot** uppdaterar pip-paket, GitHub Actions och Docker-basimagen varje vecka.

Bilddata: © Trafikverket. Kartdata: © OpenStreetMap-bidragsgivare.
