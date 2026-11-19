# Projektminne

Löpande minne för trv-webcams: vad som har gjorts, varför, och sådant som är bra att
veta. Nyaste överst i varje avsnitt. Uppdateras i samma PR som ändringen.

## Ändringslogg

| Version | Datum | Issue | PR | Ändring |
|---------|-------|-------|----|---------|
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

## Bra att veta

- Användarens server: `/opt/trv-webcams`. Den kör `ghcr.io/tubalainen/trv-webcams:latest`
  via compose med NVIDIA-GPU (`deploy`-blocket), och kodaren `h264_nvenc` fungerar där.
- Sparade bilder: `/data/snapshots/{kamera}/{ÅÅÅÅ-MM-DD}/{HHMMSS}.jpg` i lokal tid
  (`TIMEZONE`). Arkivet `/data/archive` har en bild per timme i 365 dagar
  (hårdlänkar).
- Bläddringen visar bara `snapshots` (90 dagar). Arkivbilder ingår inte än.
- Molnsandlådan har ingen GPU, och `docker build` stoppas av proxyn vid pip.
  Docker-bygget verifieras därför i CI.
