"""Admin page API: password login and global settings."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app import cameras, settings_store, weather
from app.config import get_settings
from app.db import Camera, session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin")

COOKIE = "trv_admin"
SESSION_SECONDS = 7 * 86400
FAILED_LOGIN_DELAY = 1.0


def _sign(expires: int) -> str:
    password = get_settings().admin_password
    key = hashlib.sha256((settings_store.admin_secret() + password).encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def make_token(now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_SECONDS)
    return f"{expires}.{_sign(expires)}"


def valid_token(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token or not get_settings().admin_password:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or int(exp_s) < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _sign(int(exp_s)))


def require_admin(request: Request) -> None:
    if not get_settings().admin_password:
        raise HTTPException(403, "Admin är avstängt. Sätt ADMIN_PASSWORD i .env och starta om.")
    if not valid_token(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Logga in som admin")


@router.get("/session")
def session(request: Request) -> dict:
    enabled = bool(get_settings().admin_password)
    return {"enabled": enabled, "logged_in": enabled and valid_token(request.cookies.get(COOKIE))}


class Login(BaseModel):
    password: str


@router.post("/login")
async def login(body: Login, request: Request, response: Response) -> dict:
    password = get_settings().admin_password
    if not password:
        raise HTTPException(403, "Admin är avstängt. Sätt ADMIN_PASSWORD i .env och starta om.")
    if not hmac.compare_digest(body.password.encode(), password.encode()):
        await asyncio.sleep(FAILED_LOGIN_DELAY)  # slows down guessing
        log.warning("Failed admin login from %s", request.client.host if request.client else "?")
        raise HTTPException(401, "Fel lösenord")
    response.set_cookie(
        COOKIE,
        make_token(),
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"logged_in": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"logged_in": False}


def _settings_payload(request: Request) -> dict:
    settings = get_settings()
    env = request.app.state.env_defaults
    saved = settings_store.stored()
    with session_scope() as s:
        cams = list(s.scalars(select(Camera).where(Camera.enabled)))
        hd_enabled = sum(1 for c in cams if cameras.wants_fullsize(c, settings))
        checked = s.scalar(
            select(func.count()).select_from(Camera).where(Camera.fullsize_checked_at.is_not(None))
        )
        with_hd = s.scalar(select(func.count()).select_from(Camera).where(Camera.fullsize_width.is_not(None)))
    return {
        "groups": [{"key": k, "label": label} for k, label in settings_store.GROUPS],
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "group": f.group,
                "kind": f.kind,
                "help": f.help,
                "min": f.min,
                "max": f.max,
                "unit": f.unit,
                "choices": [{"value": v, "label": label} for v, label in f.choices],
                "value": getattr(settings, f.key),
                "env_default": env[f.key],
                "saved": f.key in saved,
            }
            for f in settings_store.FIELDS
        ],
        "info": {
            "cameras_enabled": len(cams),
            "cameras_enabled_hd": hd_enabled,
            "cameras_checked": checked or 0,
            "cameras_with_hd": with_hd or 0,
        },
    }


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_admin_settings(request: Request) -> dict:
    return _settings_payload(request)


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = {}
    reset: list[str] = []


@router.put("/settings", dependencies=[Depends(require_admin)])
async def put_admin_settings(body: SettingsUpdate, request: Request) -> dict:
    settings = get_settings()
    try:
        changed = settings_store.save(settings, body.values)
        changed |= settings_store.reset(settings, body.reset, request.app.state.env_defaults)
    except settings_store.SettingsError as e:
        raise HTTPException(400, str(e)) from None
    applied = await apply_changes(request.app, changed)
    payload = _settings_payload(request)
    payload["changed"] = sorted(changed)
    payload["applied"] = applied
    return payload


async def apply_changes(app, changed: dict[str, tuple[Any, Any]]) -> list[str]:
    """Make changed settings take effect without a restart. Returns short Swedish notes."""
    settings = get_settings()
    notes: list[str] = []
    sched = getattr(app.state, "scheduler", None)
    timing = {"collect_interval_minutes", "temp_collect_interval_minutes", "nightly_hour", "nightly_minute"}
    if sched is not None and timing & changed.keys():
        from app.scheduler import reschedule

        reschedule(sched, settings)
        notes.append("Schemat är uppdaterat.")
    jobs = app.state.jobs
    if "hwaccel" in changed:
        jobs.encoder.reset()
        choice = await asyncio.to_thread(lambda: jobs.encoder.choice)
        notes.append(f"Ny GPU-kontroll: {choice.codec} ({'CPU' if choice.mode == 'cpu' else 'GPU'}).")
    if "max_concurrent_jobs" in changed:
        jobs.resize(settings.max_concurrent_jobs)
        notes.append(f"{settings.max_concurrent_jobs} jobb kan köras samtidigt.")
    if "temp_station_max_km" in changed:
        linked = await asyncio.to_thread(weather.assign_stations, settings)
        notes.append(f"{linked} kameror har en vägväderstation inom {settings.temp_station_max_km:g} km.")
    if "trv_fullsize" in changed:
        notes.append("Gäller nya bilder från nästa hämtning.")
    return notes
