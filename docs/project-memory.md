# Projektminne

Löpande minne för trv-webcams: vad som har gjorts, varför, och sådant som är bra att
veta. Nyaste överst i varje avsnitt. Uppdateras i samma PR som ändringen.

## Ändringslogg

| Version | Datum | Issue | PR | Ändring |
|---------|-------|-------|----|---------|
| 0.1.6 | 2026-11-19 | #18 | #19 | Generering på begäran (kamera/län/hela Sverige, kalender, MP4/GIF, längd), klippbibliotek, ingen nattlig generering |
| 0.1.5 | 2026-11-19 | #16 | #17 | Rensa allt, ta bort äldre än X, radering per kamera, dag och bild (admin) |
| 0.1.4 | 2026-11-19 | #13 | #15 | Admin-sida med lösenord, globala inställningar i databasen, ändringar utan omstart |
| 0.1.4 | 2026-11-19 | #14 | #15 | Hög-/lågupplösta bilder: standard i admin, val per kamera, HD-märkning med faktisk upplösning |
| 0.1.3 | 2026-11-19 | #11 | #12 | Graf med vägtemperatur i timelapse och GIF, av/på per kamera |
| 0.1.3 | 2026-11-19 | – | #10 | Python 3.15 (ersätter Dependabot #1), pip tas bort oberoende av version |
| 0.1.2 | 2026-11-19 | #8 | #6 | Bläddra fram/tillbaka bland sparade bilder på kamerasidan |
| 0.1.2 | 2026-11-19 | #9 | #6 | GPU-/hårdvarukontroll vid start, rapport i `docker compose logs` |
| 0.1.2 | 2026-11-19 | – | #6 | CLAUDE.md, projektminne, hemlighetskontroll i CI |
| 0.1.1 | 2026-11-19 | #7 | #5 | Startskript rättar ägaren på `/data` (PermissionError vid start) |
| 0.1.0 | 2026-11-19 | – | #2–#4 | Första versionen, release-flöde med automatisk tagg och release |

## Beslut

- **Spårbarhet:** varje förbättring eller bugg får ett issue, och PR:en stänger det
  med `Closes #N`. Gäller från 2026-11-19. Issue #7–#9 skapades i efterhand.
- **Hemligheter:** får aldrig pushas. `scripts/check-secrets.sh` körs i CI och före
  varje push.
- **Release:** en merge till main med en ny version i `app/__init__.py` släpper
  automatiskt. Version och tagg måste stämma.
- **Rättigheter i containern:** startskriptet körs som root, kör `chown` på `/data`
  bara när något har fel ägare, och startar sedan appen med `setpriv` som
  `PUID:PGID` utan capabilities. `user:` i compose hoppar över det steget.
- **GPU:** `HWACCEL=auto` provar NVENC → VAAPI → QSV → CPU med en riktig
  provkodning vid start. GIF görs alltid på CPU.
- **Ingen automatisk generering (beslut 2026-11-19, 0.1.6):**
  - Timelapses skapas bara när någon trycker på Generera. Den nattliga cron-tiden används nu bara för retention ("Daglig rensning").
  - Varje generering ger ett eget klipp (tabellen `clips`, filer `timelapses/{kamera}/{id}.mp4|gif`).
  - Län och hela Sverige ger ett klipp per aktiv kamera med bilder i spannet (tabellen `batches`, max 500) och kräver admin. En enskild kamera är öppen för alla.
  - Längden väljs (10/30/60/120 s), och bilderna väljs jämnt över spannet. Dagar utan snapshots tas från timarkivet.
  - Gamla rader i `timelapses` migreras till `clips` vid start (`db._migrate_timelapses_to_clips`).
  - Klippfiler slås upp med filnamnet under den aktuella `timelapses`-katalogen (`storage.clip_file`), så att lagrade absoluta sökvägar inte låser `DATA_DIR`.

- **Vägtemperatur:** `Surface.Temperature` från Trafikverkets vägväderstationer. Kameran kopplas
  till närmaste station som *har* vägytesensor (40 av 831 stationer mäter bara luft) inom
  20 km. Trafikverket sparar bara cirka 7 dygn, så appen sparar själv i tabellen
  `road_temperatures` (365 dagar). Grafen ritas med Pillow, en PNG per videoruta (identiska
  rutor delar fil), och läggs på med ffmpeg `overlay` före kodningen. NVENC påverkas alltså inte.
- **Typsnitt:** Pillows inbyggda typsnitt saknar å, ä, ö och "−". Imagen installerar därför
  `fonts-dejavu-core`, som också installeras i CI.
- **Upplösning:** källbilder som är smalare än 768 px skalas upp med en heltalsfaktor, så att
  grafens text går att läsa (normalbilderna är 384×216).
- **Databas:** `db._add_missing_columns` lägger till nya kolumner i befintliga databaser. Nya
  kolumner måste vara nullable eller ha `server_default`.

- **Admin:** `ADMIN_PASSWORD` i `.env`, utan den är admin avstängt. Sessionen är en HMAC-signerad
  cookie (7 dagar). Nyckeln är en slumpad hemlighet i `app_settings` plus lösenordet, så
  ett lösenordsbyte loggar ut alla. Bara `/api/admin/*` är skyddat, resten av appen är öppen
  (beslut 2026-11-19).
- **Inställningar:** `app/settings_store.py` listar vad som kan ändras i admin (`FIELDS`).
  Värden sparas som JSON i `app_settings` och sätts på den delade `Settings`-instansen, så
  all kod läser fortfarande `settings.x`. `.env` är startvärdet. Ändringar görs direkt:
  `scheduler.reschedule`, `Encoder.reset`, `JobQueue.resize` och `weather.assign_stations`.
- **Val per kamera:** `temp_overlay_mode` och `resolution_mode` är "default", "on/high" eller
  "off/low". Logiken finns i `app/cameras.py`. Kolumnen `temp_overlay` från 0.1.3 ligger kvar i
  gamla databaser, och "av" konverterades till `temp_overlay_mode='off'`.
- **Högupplöst:** `?type=fullsize`. Upplösningen läses ur JPEG-huvudet: servern ignorerar
  Range, så de första 16 kB strömmas och strömmen avbryts. Det görs en gång i veckan per kamera
  (cirka 2,5 min för alla), vid start och efter den dagliga synken. Uppmätt 2026-11-19 bland
  1 559 kameror: 782 har 1280×720, 759 har 1920×1080, några har 576p–960p och 1 har ingen
  högupplöst bild trots `HasFullSizePhoto`. 30 kameror (inaktiva hos Trafikverket) saknar
  `HasFullSizePhoto`.

- **Radering:** all logik finns i `app/cleanup.py`, och API:t `/api/admin/*` kräver admin.
  "Rensa allt" behåller kameror, val per kamera, väderstationer och inställningar, men tömmer
  `snapshots/`, `archive/`, `timelapses/`, `timelapses`, `jobs` och `road_temperatures`. Den
  håller insamlingens lås medan den kör. Dagar tas bort i både `snapshots` och `archive`.
  Antal och frigjorda byte räknas per inode, så en arkivlänk räknas inte som en extra bild.
  Pågående jobb för kameran ger 409. "Äldre än" för timelapses gäller när klippet skapades
  (`created_at`). Sedan 0.1.6 gäller timelapse-radering klipp, och "Rensa allt" tömmer också
  `clips` och `batches`.

## Bra att veta

- Användarens server: `/opt/trv-webcams`. Den kör `ghcr.io/tubalainen/trv-webcams:latest`
  via compose med NVIDIA-GPU (`deploy`-blocket), och kodaren `h264_nvenc` fungerar där.
- Sparade bilder: `/data/snapshots/{kamera}/{ÅÅÅÅ-MM-DD}/{HHMMSS}.jpg` i lokal tid
  (`TIMEZONE`). Arkivet `/data/archive` har en bild per timme i 365 dagar
  (hårdlänkar).
- Bläddringen visar bara `snapshots` (90 dagar). Arkivbilder ingår inte än.
- Trafikverkets bild-URL är fast per kamera och visar alltid den senaste bilden. Det finns
  ingen bildhistorik i API:t. Trafikflödeskameror uppdateras var 1–2 minut, väglagskameror var 5–10 minut.
- Molnsandlådan har ingen GPU, och `docker build` stoppas av proxyn vid pip.
  Docker-bygget verifieras därför i CI.
