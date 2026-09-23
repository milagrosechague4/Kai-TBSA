"""Google Calendar tool for Kai — per-user OAuth credentials.

Each user connects their own Google account once via /oauth/google/start.
Their refresh token is stored in data/<tenant>/oauth/<user>_calendar.json.
Calendar data is never written to the brain nor shared between users.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from ..config import Settings
from ..identity import current_context

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

_NOT_CONFIGURED = {
    "error": (
        "Google Calendar no disponible. "
        "Configurar KAI_GOOGLE_OAUTH_CLIENT_ID y KAI_GOOGLE_OAUTH_CLIENT_SECRET "
        "como variables de entorno en Railway."
    )
}


def creds_path(data_root: Path, tenant: str, user: str) -> Path:
    """On-disk path for a user's OAuth token JSON. Created on first write."""
    p = data_root / tenant / "oauth" / f"{user}_calendar.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_user_creds(data_root: Path, tenant: str, user: str):
    """Load + auto-refresh Google OAuth credentials for a user.

    Returns a google.oauth2.credentials.Credentials object if the user has
    connected their calendar, or None if they haven't yet.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    p = creds_path(data_root, tenant, user)
    if not p.exists():
        return None

    data = json.loads(p.read_text(encoding="utf-8"))
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_user_creds(data_root, tenant, user, creds, data["client_id"], data["client_secret"])
    return creds


def save_user_creds(
    data_root: Path,
    tenant: str,
    user: str,
    creds,
    client_id: str,
    client_secret: str,
) -> None:
    """Persist OAuth credentials to disk. Called after first auth and on token refresh."""
    p = creds_path(data_root, tenant, user)
    p.write_text(
        json.dumps({
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": list(creds.scopes or CALENDAR_SCOPES),
        }),
        encoding="utf-8",
    )


def register_calendar_tools(mcp: FastMCP, settings: Settings) -> None:

    @mcp.tool
    async def kai_calendar(days_ahead: int = 7) -> dict:
        """Fetch upcoming events from the authenticated user's personal Google Calendar.

        Returns events from now until days_ahead days from now (default: 7), sorted by
        start time. Only reads the calling user's own calendar — data is never written
        to the brain or visible to other users.

        If the user hasn't connected their Google Calendar yet, returns instructions
        with the URL to authorize (one-time setup, takes ~30 seconds).
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if creds is None:
                return {
                    "error": "Tu Google Calendar no está conectado todavía.",
                    "accion": (
                        f"Visitá esta URL para conectarlo (solo necesitás hacerlo una vez): "
                        f"{settings.oauth_base_url}/oauth/google/start"
                        f"?token=<tu-token-kai>"
                    ),
                }

            from googleapiclient.discovery import build

            svc = build("calendar", "v3", credentials=creds, cache_discovery=False)

            now = datetime.now(timezone.utc)
            until = now + timedelta(days=days_ahead)

            result = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=until.isoformat(),
                    maxResults=50,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            events = result.get("items", [])
            formatted = []
            for e in events:
                start = e.get("start", {})
                end = e.get("end", {})
                formatted.append({
                    "titulo": e.get("summary", "(sin título)"),
                    "inicio": start.get("dateTime") or start.get("date", ""),
                    "fin": end.get("dateTime") or end.get("date", ""),
                    "ubicacion": e.get("location", ""),
                    "descripcion": (e.get("description") or "")[:300],
                    "link": e.get("htmlLink", ""),
                    "todo_el_dia": "date" in start and "dateTime" not in start,
                })

            return {
                "usuario": ctx.user,
                "periodo_dias": days_ahead,
                "total": len(formatted),
                "eventos": formatted,
            }

        return await asyncio.to_thread(_fetch)
