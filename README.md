# trv-webcams

Webbapplikation som sparar bilder från Trafikverkets webbkameror och skapar timelapses (MP4) och GIF-animationer, med GPU-acceleration via ffmpeg (primärt NVIDIA NVENC).

- **Karta och lista** över alla ca 1 600 kameror i Sverige, med sökning och filter per län.
- **Du väljer själv vilka kameror som är aktiva.** Bara aktiva kameror sparar bilder och får timelapses, så att du inte lagrar hela Sveriges kameranät. Du kan också aktivera eller avaktivera alla kameror i ett län på en gång.
- **Timelapses på begäran:** välj en kamera, de aktiva kamerorna i ett län eller alla aktiva kameror, ett tidsspann (förval eller fritt i en kalender), MP4, GIF eller båda, och längd. Tryck sedan på **Generera**. Klippen sparas i ett bibliotek per kamera. Inget genereras automatiskt.
- **Graf med vägtemperatur** i timelapses och GIF:ar. Grafen hämtar data från närmaste vägväderstation inom 20 km och ritas fram bild för bild. Den är påslagen som standard och kan stängas av per kamera.
- **Retention:** alla bilder sparas i 90 dagar. Den första bilden varje timme sparas i ett glest arkiv i 365 dagar, så att det går att göra årsklippet.
- **Admin-sida** (`/admin.html`, skyddad med `ADMIN_PASSWORD`) där globala inställningar ändras direkt, utan omstart.
- **Hög- eller lågupplösta bilder** (1280×720/1920×1080 eller 385×217). Standarden sätts i admin och kan ändras per kamera. Listan och kartan märker kameror som har högupplösta bilder med till exempel "HD 1080p".
- Webbgränssnittet lyssnar på port **8933** och är på svenska. Versionen som körs visas i toppraden, och "Endast aktiva" är förvalt i listan när du har aktiverat kameror.

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
/data/timelapses/{kamera}/{klipp-id}.mp4|.gif       klippbiblioteket
```

Containern startar som root, sätter ägare på `/data` till `PUID:PGID` (standard `1000:1000`) och kör sedan appen som den användaren. En `./data`-mapp som Docker har skapat som root fungerar därför direkt. Ägaren ändras bara när något i `/data` har fel ägare. Kör du containern med `user:` i compose eller `--user` hoppas det steget över, och då måste mappen redan vara skrivbar för den användaren.

## Generera timelapses

Timelapses skapas bara när någon trycker på **Generera**. Det finns ingen automatisk nattkörning sedan 0.1.6.

- **En kamera** (kamerasidan, öppen för alla):
  - **Tidsspann:** välj ett förval (senaste dygnet, 7, 14 eller 31 dygn, kvartal, år) eller ett eget spann i kalendern, med start- och sluttid. Kalendern markerar dagar med bilder: mörkare för alla bilder, ljusare för dagar som bara har en bild per timme.
    Man kan inte välja längre bak än den första sparade bilden, och förval som skulle börja tidigare går inte att välja. Finns inga bilder alls är rutan spärrad.
  - **Övrigt:** välj MP4, GIF eller båda, längd (10, 30, 60 eller 120 s) och temperaturgraf (standard, på, av).
  - **Bibliotek:** klippet hamnar i kamerans bibliotek, där det kan spelas upp och laddas ner. Alla kan ta bort klippets MP4, GIF eller hela klippet, eftersom det går att generera igen. Bilder tas bara bort i admin.
- **Alla aktiva kameror eller de aktiva i ett län** (admin-sidan, kräver inloggning):
  - Avsnittet **Generera** i admin har samma genereringsruta och ett val av urval: "Alla aktiva kameror" eller ett län. Varje val visar hur många aktiva kameror det gäller, och län utan aktiva kameror går inte att välja.
  - Varje aktiv kamera som har bilder i tidsspannet får ett eget klipp, och kameror utan bilder hoppas över.
  - Förloppet visas som en stapel under rutan, och en notis visas när körningen är klar. En körning får omfatta högst 500 kameror.
- **Bilder:** bilderna fördelas jämnt över spannet. För dagar äldre än bildernas retention (90 dagar) används årsarkivet, med en bild per timme. Spannet kan därför vara upp till 365 dagar långt.
- **Från tidigare versioner:** klipp som skapades av nattkörningen i 0.1.0–0.1.5 flyttas automatiskt in i biblioteket.

## Admin

Admin-sidan (`⚙ Admin` i toppraden) har en sidomeny med fem avsnitt. På mobil blir menyn flikar. Adressen följer avsnittet (till exempel `admin.html#lagring`), så att länkar och bakåtknappen fungerar.

| Avsnitt | Innehåll |
|---------|----------|
| **Översikt** | Startsidan, som uppdateras varje minut. Visar aktiva kameror, lagring och disk (med en uppskattning vid full retention), videokodare/GPU, jobbkö, nästa schemalagda körningar och de senaste genereringarna. Varningar visas för saknad `TRV_API_KEY`, lite diskutrymme, en disk som inte räcker vid full retention, misslyckade genereringar och inga aktiva kameror. |
| **Generera** | Klipp för alla aktiva kameror eller de aktiva i ett län (se ovan). |
| **Lagring** | Lagring per kamera, kameravyn och "Ta bort allt som är äldre än" (se nedan). |
| **Inställningar** | De globala inställningarna i fyra kort. |
| **Underhåll** | Rensa allt och börja om. |

Inställningarna är uppdelade i fyra kort:
- **Kameror:** standard för bildupplösning och temperaturgraf.
- **Insamling:** hur ofta bilder och vägtemperatur hämtas, och avstånd till vägväderstation.
- **Lagring och rensning:** retention och tid för den dagliga rensningen.
- **Video och GIF:** hårdvaruacceleration, samtidiga jobb och video- och GIF-inställningar.

När något ändras markeras raden, och en list längst ner visar antalet osparade ändringar med knapparna **Ångra** och **Spara**. Lämnar du sidan eller avsnittet med osparade ändringar får du en fråga. Inställningar som har ändrats i admin märks "Ändrad i admin" och kan återställas till `.env`-värdet.

Allt som tar bort data frågar först i en dialog som sammanfattar vad som tas bort. Resultatet visas som en notis.

Ändringarna sparas i databasen och gäller direkt. Schemat uppdateras, en ändrad hårdvaruacceleration gör en ny GPU-kontroll, och antalet jobb ändras när pågående jobb är klara. Värdena i `.env` används som standard tills de ändras i admin.

Admin kräver ett lösenord:

```bash
ADMIN_PASSWORD=ett-långt-lösenord   # i .env, starta sedan om containern
```

Utan `ADMIN_PASSWORD` är admin-sidan avstängd. Inloggningen gäller i 7 dagar (HttpOnly-cookie), och den upphör om lösenordet byts. Resten av appen är öppen som tidigare. Kör den i ett betrott nät eller bakom en egen proxy. Hemligheter och infrastruktur (`TRV_API_KEY`, `ADMIN_PASSWORD`, `DATA_DIR`, `PORT`, `PUID`/`PGID`, `TIMEZONE`) sätts bara i `.env`.

### Rensa och ta bort data

Avsnitten **Lagring** och **Underhåll** på admin-sidan har de här verktygen:

- **Ta bort allt som är äldre än:**
  - Välj ett datum, eller snabbvalen 7, 30 eller 90 dagar.
  - Välj bilder, timelapses eller båda, och alla kameror eller en kamera.
  - En förhandsräkning visar hur mycket som tas bort, och en dialog frågar innan något tas bort.
  - Bilder räknas per dag, och klipp efter när de skapades.
- **Lagring per kamera:** en sammanfattning och en sökbar, sorterbar tabell (störst, flest bilder, äldst, flest klipp eller namn) med bilder, dagar, storlek och klipp per kamera.
- **Kameravy:** klicka på en kamera i tabellen. Vyn ersätter tabellen, och "← Alla kameror med data" leder tillbaka.
  - **Kalender:** dagar är färgade efter mängden bilder, ljusblå för dagar som bara har timbilder.
  - **Markering:** ett klick markerar en dag, och skift-klick ett intervall. Summan av bilder och storlek visas.
  - **Miniatyrer:** "Visa bilder" visar miniatyrerna för dagen. Ett klick förstorar bilden, och där kan den tas bort.
  - **Knappar:** ta bort markerade dagar, bilder äldre än ett datum, alla bilder eller alla klipp.
- **Rensa allt och börja om** (Underhåll): tar bort alla bilder, timelapses, sparad vägtemperatur och jobbhistorik.
  - Aktiva kameror, valen per kamera och inställningarna ligger kvar, så insamlingen fortsätter vid nästa hämtning.
  - Knappen fungerar först när du skriver `RENSA ALLT`.

Bilder tas bara bort här i admin, aldrig på kamerasidan. De tas bort både i `snapshots` och i årsarkivet. Arkivet är hårdlänkar, så utrymmet frigörs först när båda är borta. Pågår ett timelapse-jobb för kameran får du vänta tills det är klart.

## Hög- eller lågupplösta bilder

| | Lågupplöst | Högupplöst |
|---|---|---|
| Trafikflödeskameror | 385×217, cirka 28 kB | oftast 1280×720, cirka 130 kB |
| Väglagskameror | 385×217, cirka 14–28 kB | 1920×1080, cirka 160 kB |

Appen läser den faktiska upplösningen på varje kamerans högupplösta bild en gång i veckan. Den läser bara JPEG-huvudet, inte hela bilden. Kameror med högupplöst bild märks till exempel "HD 1080p" i listan och kartan, och märket är grönt när kameran sparar högupplöst. Kamerasidan visar upplösningen och vad som sparas.

Standarden sätts i admin. På kamerasidan väljer du "Standard", "Högupplöst" eller "Lågupplöst" för just den kameran. Har kameran ingen högupplöst bild sparas lågupplöst. Valet gäller nya bilder. Timelapse-videon får samma upplösning som bilderna, och GIF:en förblir 480 px bred. Högupplöst tar ungefär 5 gånger mer plats, cirka 3,6 GB per kamera under 90 dagar med en bild var 5:e minut, och lagringsuppskattningen i statusraden räknar med det.

## Vägtemperatur i timelapses

Nere till höger i varje timelapse och GIF visas en liten graf över **vägytans temperatur**:
- X-axeln täcker hela klippets period, och kurvan ritas fram till den aktuella bildens tidpunkt.
- Det aktuella värdet visas blått vid 0 °C och kallare, annars orange.
- En streckad linje markerar 0 °C, och längst ner står periodens lägsta och högsta värde.

- **Datakälla:** Trafikverkets vägväderstationer (`WeatherMeasurepoint`/`WeatherObservation`). Varje kamera kopplas till närmaste station *som mäter vägytans temperatur* inom `TEMP_STATION_MAX_KM` (standard 20 km). Stationen och det senaste värdet visas på kamerasidan.
- **Historik:** Trafikverket sparar bara cirka 7 dygns mätningar. Appen hämtar därför vägtemperaturen var 15:e minut för stationerna som hör till aktiva kameror och sparar den i 365 dagar, lika länge som årsarkivet. När en kamera aktiveras hämtas de senaste 7 dygnen direkt. Grafer för längre perioder fylls på med tiden, och det som saknas lämnas tomt.
- **Av och på:** grafen är påslagen som standard (i admin). På kamerasidan väljer du "Standard", "På" eller "Av" för just den kameran. I genereringsrutan kan du också välja för just det klippet.
- **Upplösning:** Trafikverkets normala bilder är bara cirka 384 pixlar breda. Videon skalas därför upp till minst 768 pixlars bredd, så att grafens text går att läsa.

## Konfiguration

Alla inställningar finns i [`.env.example`](.env.example). Allt utom hemligheter och infrastruktur kan också ändras på admin-sidan, och det som sparas där gäller före `.env`. De viktigaste:

| Variabel | Standard | Beskrivning |
|----------|----------|-------------|
| `TRV_API_KEY` | – | **Krävs.** Trafikverkets API-nyckel |
| `ADMIN_PASSWORD` | – | Lösenord för admin-sidan. Tomt stänger av admin |
| `HOST_PORT` | `8933` | Port på värden (docker compose) |
| `COLLECT_INTERVAL_MINUTES` | `5` | Hur ofta bilder hämtas |
| `TRV_FULLSIZE` | `false` | Standard för bildupplösning: högupplöst (`true`) eller lågupplöst |
| `TEMP_OVERLAY_DEFAULT` | `true` | Standard för temperaturgraf i timelapses |
| `SNAPSHOT_RETENTION_DAYS` | `90` | Hur länge alla bilder sparas |
| `ARCHIVE_RETENTION_DAYS` | `365` | Hur länge timarkivet sparas |
| `NIGHTLY_HOUR` / `NIGHTLY_MINUTE` | `2` / `0` | När den dagliga rensningen (retention) körs |
| `HWACCEL` | `auto` | `auto`, `nvidia`, `vaapi`, `qsv` eller `cpu` |
| `MAX_CONCURRENT_JOBS` | `4` | Antal klipp som görs samtidigt (1–16). Fler gör generering för många kameror snabbare |
| `MAX_GPU_ENCODES` | `3` | Högst så många MP4-kodningar på GPU:n samtidigt (1–8). GeForce-kort begränsar antalet NVENC-kodningar |
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
