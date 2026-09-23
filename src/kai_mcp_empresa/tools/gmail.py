"""Google Gmail tools for Kai — per-user OAuth credentials.

Same OAuth credentials as Google Calendar (single connection covers both).
Gmail data is never written to the brain nor shared between users.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from fastmcp import FastMCP

from ..config import Settings
from ..identity import current_context
from .calendar import load_user_creds

_NOT_CONFIGURED = {
    "error": (
        "Google Gmail no disponible. "
        "Configurar KAI_GOOGLE_OAUTH_CLIENT_ID y KAI_GOOGLE_OAUTH_CLIENT_SECRET."
    )
}

_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def _has_gmail_scope(creds) -> bool:
    return creds is not None and _GMAIL_SCOPE in (creds.scopes or [])


def _reconnect_response(oauth_base_url: str) -> dict:
    return {
        "error": "Tu Gmail no está conectado (o fue autorizado antes de que se agregara esta integración).",
        "accion": (
            f"Visitá esta URL para conectar Gmail + Calendar juntos (solo una vez): "
            f"{oauth_base_url}/oauth/google/start?token=<tu-token-kai>"
        ),
    }


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload: dict) -> str:
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        for part in payload["parts"]:
            text = _decode_body(part)
            if text:
                return text
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    return ""


def register_gmail_tools(mcp: FastMCP, settings: Settings) -> None:

    @mcp.tool
    async def kai_gmail_search(query: str, max_results: int = 10) -> dict:
        """Search emails in the authenticated user's Gmail using Gmail query syntax.

        Supports standard Gmail operators:
          - "from:alguien@tbsa.ar"
          - "subject:propuesta is:unread"
          - "has:attachment newer_than:7d"
          - "to:me newer_than:3d"

        Returns up to max_results messages (default 10, max 50).
        Use the returned thread_id with kai_gmail_thread to read the full content.
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if not _has_gmail_scope(creds):
                return _reconnect_response(settings.oauth_base_url)

            from googleapiclient.discovery import build

            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)

            result = svc.users().messages().list(
                userId="me",
                q=query,
                maxResults=min(max_results, 50),
            ).execute()

            messages = result.get("messages", [])
            if not messages:
                return {"query": query, "total": 0, "mensajes": []}

            formatted = []
            for msg in messages:
                full = svc.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                ).execute()
                headers = full.get("payload", {}).get("headers", [])
                formatted.append({
                    "id": full["id"],
                    "thread_id": full["threadId"],
                    "de": _header(headers, "From"),
                    "para": _header(headers, "To"),
                    "asunto": _header(headers, "Subject"),
                    "fecha": _header(headers, "Date"),
                    "snippet": full.get("snippet", ""),
                    "leido": "UNREAD" not in full.get("labelIds", []),
                })

            return {"query": query, "total": len(formatted), "mensajes": formatted}

        return await asyncio.to_thread(_fetch)

    @mcp.tool
    async def kai_gmail_thread(thread_id: str) -> dict:
        """Read a complete Gmail thread by its thread_id.

        Returns all messages in the thread with decoded body text
        (first 2000 characters per message).

        Get thread_id from kai_gmail_search or kai_gmail_inbox results.
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if not _has_gmail_scope(creds):
                return _reconnect_response(settings.oauth_base_url)

            from googleapiclient.discovery import build

            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)

            thread = svc.users().threads().get(
                userId="me", id=thread_id, format="full"
            ).execute()

            messages = []
            for msg in thread.get("messages", []):
                headers = msg.get("payload", {}).get("headers", [])
                body = _decode_body(msg.get("payload", {}))
                messages.append({
                    "id": msg["id"],
                    "de": _header(headers, "From"),
                    "para": _header(headers, "To"),
                    "asunto": _header(headers, "Subject"),
                    "fecha": _header(headers, "Date"),
                    "cuerpo": body[:2000],
                    "leido": "UNREAD" not in msg.get("labelIds", []),
                })

            return {
                "thread_id": thread_id,
                "total_mensajes": len(messages),
                "mensajes": messages,
            }

        return await asyncio.to_thread(_fetch)

    @mcp.tool
    async def kai_gmail_inbox(days: int = 3) -> dict:
        """Summarize the authenticated user's Gmail inbox for the last N days (default: 3).

        Returns up to 30 recent inbox messages sorted by date, with sender, subject,
        read status, and a short snippet. Does not return full email bodies —
        use kai_gmail_thread with the thread_id for complete content.
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if not _has_gmail_scope(creds):
                return _reconnect_response(settings.oauth_base_url)

            from googleapiclient.discovery import build

            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)

            result = svc.users().messages().list(
                userId="me",
                q=f"in:inbox newer_than:{days}d",
                maxResults=30,
            ).execute()

            messages = result.get("messages", [])
            if not messages:
                return {"usuario": ctx.user, "periodo_dias": days, "total": 0, "mensajes": []}

            formatted = []
            for msg in messages:
                full = svc.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                headers = full.get("payload", {}).get("headers", [])
                formatted.append({
                    "id": full["id"],
                    "thread_id": full["threadId"],
                    "de": _header(headers, "From"),
                    "asunto": _header(headers, "Subject"),
                    "fecha": _header(headers, "Date"),
                    "snippet": full.get("snippet", ""),
                    "leido": "UNREAD" not in full.get("labelIds", []),
                })

            return {
                "usuario": ctx.user,
                "periodo_dias": days,
                "total": len(formatted),
                "mensajes": formatted,
            }

        return await asyncio.to_thread(_fetch)
