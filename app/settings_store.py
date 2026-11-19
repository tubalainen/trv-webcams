"""Global settings that can be changed on the admin page.

Environment variables (app.config.Settings) are the start values. Values saved on the
admin page are stored in the database (table app_settings) and win over them. They are
applied to the shared Settings instance, so every module keeps reading `settings.x`.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import AppSetting, session_scope

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str  # int | float | bool | choice
    help: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple[tuple[str, str], ...] = ()
    unit: str = ""


GROUPS = [
    ("cameras", "Standard för kameror"),
    ("collect", "Hämtning"),
    ("nightly", "Daglig rensning"),
    ("retention", "Lagring och retention"),
    ("encoding", "Videokodning"),
    ("gif", "GIF"),
    ("weather", "Vägtemperatur"),
]

FIELDS: list[Field] = [
    Field(
        "trv_fullsize",
        "Bildupplösning",
        "cameras",
        "bool",
        "Högupplöst ger 1280×720 eller 1920×1080 i stället för 385×217, men ungefär 5 gånger mer "
        "lagring. Varje kamera kan välja något annat än standarden.",
        choices=(("false", "Lågupplöst (385×217)"), ("true", "Högupplöst (max)")),
    ),
    Field(
        "temp_overlay_default",
        "Temperaturgraf i timelapses",
        "cameras",
        "bool",
        "Graf med vägtemperatur nere till höger i MP4 och GIF. Varje kamera kan välja något annat.",
        choices=(("true", "På"), ("false", "Av")),
    ),
    Field(
        "collect_interval_minutes",
        "Hämta bilder var",
        "collect",
        "int",
        "Trafikflödeskameror uppdateras var 1–2 minut, väglagskameror var 5–10 minut.",
        1,
        60,
        unit="min",
    ),
    Field(
        "nightly_hour",
        "Timme",
        "nightly",
        "int",
        "När bilder och arkiv äldre än retentionen tas bort varje dygn. Timelapses skapas bara när "
        "någon trycker på Generera.",
        0,
        23,
    ),
    Field("nightly_minute", "Minut", "nightly", "int", "", 0, 59),
    Field(
        "snapshot_retention_days",
        "Spara alla bilder i",
        "retention",
        "int",
        "Används för timelapses upp till 31 dygn.",
        1,
        3650,
        unit="dagar",
    ),
    Field(
        "archive_retention_days",
        "Spara timarkivet i",
        "retention",
        "int",
        "En bild per timme, för kvartals- och årsklipp. Gäller också sparad vägtemperatur.",
        1,
        3650,
        unit="dagar",
    ),
    Field(
        "hwaccel",
        "Hårdvaruacceleration",
        "encoding",
        "choice",
        "Auto provar NVIDIA → VAAPI → QSV → CPU. En ändring gör en ny kontroll av GPU:n.",
        choices=(
            ("auto", "Auto"),
            ("nvidia", "NVIDIA (NVENC)"),
            ("vaapi", "VAAPI (Intel/AMD)"),
            ("qsv", "Intel Quick Sync"),
            ("cpu", "CPU (libx264)"),
        ),
    ),
    Field(
        "max_concurrent_jobs", "Samtidiga jobb", "encoding", "int", "Antal timelapse-jobb åt gången.", 1, 8
    ),
    Field("output_fps", "Bilder per sekund i video", "encoding", "int", "", 5, 60, unit="fps"),
    Field("gif_width", "Bredd", "gif", "int", "", 160, 1280, unit="px"),
    Field("gif_fps", "Bilder per sekund", "gif", "int", "", 1, 30, unit="fps"),
    Field("gif_max_frames", "Max antal bilder", "gif", "int", "Större värde ger större filer.", 10, 1000),
    Field(
        "temp_station_max_km",
        "Största avstånd till väderstation",
        "weather",
        "float",
        "Kameror utan vägväderstation inom avståndet får ingen graf.",
        1,
        200,
        unit="km",
    ),
    Field(
        "temp_collect_interval_minutes",
        "Hämta vägtemperatur var",
        "weather",
        "int",
        "Stationerna mäter var 5:e minut.",
        5,
        180,
        unit="min",
    ),
]
FIELDS_BY_KEY = {f.key: f for f in FIELDS}

_SECRET_KEY = "_admin_secret"


class SettingsError(ValueError):
    pass


def coerce(field: Field, value: Any) -> Any:
    """Validate and convert a value from the admin form."""
    if field.kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "1", "on", "yes"):
            return True
        if str(value).lower() in ("false", "0", "off", "no"):
            return False
        raise SettingsError(f"{field.label}: ogiltigt värde")
    if field.kind == "choice":
        allowed = [c[0] for c in field.choices]
        if str(value) not in allowed:
            raise SettingsError(f"{field.label}: välj ett av {', '.join(allowed)}")
        return str(value)
    try:
        num = int(value) if field.kind == "int" else float(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{field.label}: ange ett tal") from None
    if field.kind == "int" and isinstance(value, float) and not value.is_integer():
        raise SettingsError(f"{field.label}: ange ett heltal")
    if field.min is not None and num < field.min or field.max is not None and num > field.max:
        raise SettingsError(f"{field.label}: ska vara mellan {field.min:g} och {field.max:g}")
    return num


def stored() -> dict[str, Any]:
    """Values saved on the admin page (only known keys)."""
    with session_scope() as s:
        rows = {r.key: r.value for r in s.query(AppSetting).all()}
    out = {}
    for key, raw in rows.items():
        if key in FIELDS_BY_KEY:
            try:
                out[key] = coerce(FIELDS_BY_KEY[key], json.loads(raw))
            except (SettingsError, ValueError):
                log.warning("Ignoring invalid stored setting %s=%r", key, raw)
    return out


def apply_stored(settings: Settings, env_defaults: dict[str, Any]) -> None:
    """Apply saved admin values on top of the environment start values."""
    for key, value in {**env_defaults, **stored()}.items():
        setattr(settings, key, value)


def env_defaults_of(settings: Settings) -> dict[str, Any]:
    return {f.key: getattr(settings, f.key) for f in FIELDS}


def save(settings: Settings, changes: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Validate, store and apply changes. Returns {key: (old, new)} for changed values."""
    unknown = [k for k in changes if k not in FIELDS_BY_KEY]
    if unknown:
        raise SettingsError(f"Okänd inställning: {', '.join(unknown)}")
    clean = {k: coerce(FIELDS_BY_KEY[k], v) for k, v in changes.items()}
    changed = {k: (getattr(settings, k), v) for k, v in clean.items() if getattr(settings, k) != v}
    with session_scope() as s:
        for key, value in clean.items():
            row = s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=json.dumps(value)))
            else:
                row.value = json.dumps(value)
    for key, (_old, new) in changed.items():
        setattr(settings, key, new)
    if changed:
        log.info("Admin changed settings: %s", ", ".join(f"{k}={v[1]!r}" for k, v in changed.items()))
    return changed


def reset(settings: Settings, keys: list[str], env_defaults: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Forget saved values so the environment start values apply again."""
    changed = {}
    with session_scope() as s:
        for key in keys:
            if key not in FIELDS_BY_KEY:
                raise SettingsError(f"Okänd inställning: {key}")
            row = s.get(AppSetting, key)
            if row is not None:
                s.delete(row)
            if getattr(settings, key) != env_defaults[key]:
                changed[key] = (getattr(settings, key), env_defaults[key])
    for key, (_old, new) in changed.items():
        setattr(settings, key, new)
    return changed


def admin_secret() -> str:
    """Random per-installation key for signing admin sessions (kept in the database)."""
    with session_scope() as s:
        row = s.get(AppSetting, _SECRET_KEY)
        if row is None:
            row = AppSetting(key=_SECRET_KEY, value=json.dumps(secrets.token_hex(32)))
            s.add(row)
        return json.loads(row.value)
