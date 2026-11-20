# Projektminne

Löpande minne för trv-webcams: vad som har gjorts, varför, och sådant som är bra att
veta. Nyaste överst i varje avsnitt. Uppdateras i samma PR som ändringen.

## Ändringslogg

| Version | Datum | Issue | PR | Ändring |
|---------|-------|-------|----|---------|
| – | 2026-11-20 | #32 | – | README: exempelklipp (`docs/example/`, GIF som länkar till MP4) och uppdaterad API-tabell. Ingen ny version, eftersom imagen inte ändras |
| 0.1.11 | 2026-11-20 | #30 | #31 | Snabbare bulkgenerering: MP4 och GIF parallellt i varje jobb, standard 8 jobb och 6 GPU-kodningar, tid per steg i jobbmeddelandet och loggen, ffmpeg dödas när en del avbryts |
| 0.1.10 | 2026-11-20 | #29 | #28 | Avsnittet Klipp i admin: galleri med alla klipp (sök, körning, format), spelare med nedladdning och radering, länkar från översikten och efter en körning. `GET /api/clips` och `GET /api/batches`. Klipp från körningar får nu `batch_id` |
| 0.1.10 | 2026-11-20 | #27 | #28 | Fler parallella genereringar: "Samtidiga jobb" upp till 16 (standard 4), ny gräns "Samtidiga GPU-kodningar" (standard 3), ett misslyckat GPU-försök kodar bara det klippet på CPU |
| 0.1.10 | 2026-11-20 | #26 | #28 | Generera: "Hela Sverige" heter "Alla aktiva kameror", och urvalet visar antal aktiva per län (län utan aktiva är spärrade) |
| 0.1.9 | 2026-11-20 | #24 | #25 | Ny layout för admin: sidomeny (flikar på mobil) med Översikt, Generera, Lagring, Inställningar och Underhåll, översikt med varningar, inställningar i fyra kort med spara-list, egna dialoger och notiser |
| 0.1.8 | 2026-11-19 | #22 | #23 | Bildradering bara i admin (sökbar tabell och kameravy med kalender och miniatyrer), klipp (MP4, GIF eller båda) raderbara för alla på kamerasidan, UI-filer med `no-cache`, "Endast aktiva" förvalt när kameror är aktiva, versionsnummer i toppraden |
| 0.1.7 | 2026-11-19 | #20 | #21 | Generering för län och hela Sverige flyttad från översikten till admin, kalendern begränsad till dagar med sparade bilder |
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
- **Parallellitet (beslut 2026-11-20, 0.1.10):**
  - **Jobb:** `max_concurrent_jobs` (1–16, standard 4) styr antalet jobb, och `max_gpu_encodes` (1–8, standard 3) styr bara själva MP4-kodningen på GPU:n (`Encoder.gpu_slot`, en `Slots`-begränsare som läser inställningen vid varje väntan). Bildurval, temperaturgraf per ruta och GIF körs på CPU i alla jobb samtidigt.
  - **Varför:** användaren såg bara cirka 10 % GPU vid bulkgenerering, så CPU-arbetet var flaskhalsen. GeForce-kort begränsar antalet samtidiga NVENC-sessioner.
  - **0.1.11:** användaren såg fortfarande låg belastning (load average 2,7 av 16 trådar, 3 ffmpeg åt gången).
    - **Orsak:** GPU-platsen omfattar hela MP4-kedjan, och mjpeg-avkodningen i ffmpeg är i praktiken enkeltrådad per jobb. Därför höjdes standardvärdena till 8 jobb och 6 GPU-kodningar.
    - **Parallellt:** MP4 och GIF körs parallellt i en `asyncio.TaskGroup`. Fel i den ena avbryter den andra, och `ffmpeg.run` dödar processen vid `CancelledError`.
    - **Mätt 2026-11-20** i sandlådan (libx264, ett dygn med 1 440 bilder i 1080p, 30 s): bilder 0,1 s, graf 1,2 s, GIF 3,3 s och MP4 13,7 s. Totalen gick från 17,0 till 14,9 s per klipp.
    - **Processpool för grafen förkastades.** Grafen är en liten del av tiden. Arbetsprocesser med forkserver eller spawn importerar dessutom `app.main` (som kör `create_app()`) en gång till.
    - **Nästa steg om MP4 fortfarande begränsar på GPU-servern:** avkodning med NVDEC (mjpeg) och `scale_cuda`. Det kan bara testas på riktig GPU.
  - **Fel på GPU:n:** misslyckas en GPU-kodning (`timelapse.encode_mp4`) görs bara det klippet om på CPU. Appen byter till CPU för gott (`Encoder.downgrade`) först när en ny provkodning också misslyckas. Tidigare räckte ett enda fel, vilket kunde flytta en hel bulkkörning till CPU.
- **Ingen automatisk generering (beslut 2026-11-19, 0.1.6):**
  - Timelapses skapas bara när någon trycker på Generera. Den nattliga cron-tiden används nu bara för retention ("Daglig rensning").
  - Varje generering ger ett eget klipp (tabellen `clips`, filer `timelapses/{kamera}/{id}.mp4|gif`).
  - Län och alla aktiva (förr "hela Sverige", bytt namn i 0.1.10 eftersom bara aktiva kameror ingår) ger ett klipp per aktiv kamera med bilder i spannet (tabellen `batches`, max 500) och kräver admin. Sedan 0.1.7 görs det bara på admin-sidan, och översikten har ingen genereringsknapp (användarens önskan). En enskild kamera är öppen för alla (kamerasidan).
  - Längden väljs (10/30/60/120 s), och bilderna väljs jämnt över spannet. Dagar utan snapshots tas från timarkivet.
  - Kalendern och förvalen (`generate.js`) går inte längre bak än den första sparade bilden (användarens önskan, 0.1.7). Utan bilder är rutan spärrad.
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

- **Admin-layout (beslut 2026-11-20, 0.1.9):** användaren tyckte att den långa admin-sidan var ologisk.
  - En sida (`admin.html`) med sidomeny och hash-routing (`#oversikt`, `#generera`, `#lagring`, `#installningar`, `#underhall`). Under 800 px blir menyn flikar.
  - **Översikt** är startsidan och använder bara befintliga API:er (`/api/status`, `/api/jobs?limit=10`, `info` från `/api/admin/settings`). Varningsgränsen för disken är under 5 GB eller under 10 % ledigt.
  - **Inställningar:** `settings_store.GROUPS` har fyra grupper (cameras, collect, retention, encoding). Visningsordningen, underrubrikerna och ihopslagningen av `nightly_hour` och `nightly_minute` till en rad finns i `admin.js` (`ORDER`, `SUBHEADS` och `PAIR`).
  - **Kontroller:** av/på med "På"/"Av" visas som switch, 2–3 val som segmentknappar och fler val som select.
  - **Spara-list:** visas vid ändringar. Byte av avsnitt med osparade ändringar ger en dialog (Stanna kvar, Släng eller Spara), och `beforeunload` varnar. "Återställ" sparar direkt men behåller andra osparade ändringar.
  - **Dialoger och notiser:** `appDialog`, `confirmDialog` och `toast` finns i `common.js`. Admin använder inte längre `window.confirm`, men övriga sidor gör det fortfarande.
- **Klipp i admin (0.1.10):** `GET /api/clips` (q, batch_id, kind, limit, offset, nyast först) och
  `GET /api/batches` (med antal klipp) är öppna, precis som klippen på kamerasidan. Radering i spelaren
  använder det öppna `DELETE /api/cameras/{id}/clips/{clip_id}?kind=`. Videominiatyrer laddas först när de
  syns (IntersectionObserver, `#t=0.5`). Före 0.1.10 fick klipp från körningar inget `batch_id`, bara
  jobbet. Det fylls i vid start från `jobs.clip_id` (`db._migrate_data`), och `run_job` sätter det nu.
- **Radering, var (beslut 2026-11-19, 0.1.8):** bilder (snapshots och arkiv) tas bara bort i admin,
  i tabellen och kameravyn under "Lagring". På kamerasidan kan **alla** ta bort klipp
  (MP4, GIF eller båda) med `DELETE /api/cameras/{id}/clips/{clip_id}?kind=`, eftersom de går att
  generera igen. Kamerasidan har inga knappar för bildradering.
- **Cache:** UI-filerna skickas med `Cache-Control: no-cache` (`main.UIStaticFiles`, inte `vendor/`).
  Efter en uppgradering kunde webbläsaren annars köra gammal JS. Det hände med kalendergränsen i 0.1.7.
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
  GPU:n är ett NVIDIA GeForce RTX 3060 (12 GB). Användaren har bekräftat 2026-11-19 (nvtop)
  att generering på begäran kodar med NVENC och går snabbt.
- Sparade bilder: `/data/snapshots/{kamera}/{ÅÅÅÅ-MM-DD}/{HHMMSS}.jpg` i lokal tid
  (`TIMEZONE`). Arkivet `/data/archive` har en bild per timme i 365 dagar
  (hårdlänkar).
- Bläddringen visar bara `snapshots` (90 dagar). Arkivbilder ingår inte än.
- Trafikverkets bild-URL är fast per kamera och visar alltid den senaste bilden. Det finns
  ingen bildhistorik i API:t. Trafikflödeskameror uppdateras var 1–2 minut, väglagskameror var 5–10 minut.
- Exempelklippet i README ligger i `docs/example/`. GitHub visar inte en incheckad MP4 i README, så README visar en GIF (480 px, 8 fps, cirka 6 MB) som länkar till MP4:an (720p, CRF 26, cirka 3 MB).
- Molnsandlådan har ingen GPU, och `docker build` stoppas av proxyn vid pip.
  Docker-bygget verifieras därför i CI.
