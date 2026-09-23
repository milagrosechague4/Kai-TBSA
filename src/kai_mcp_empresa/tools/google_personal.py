"""Google personal tools for Kai — Drive, Contacts, Tasks per-user OAuth.

Same OAuth credentials as Calendar/Gmail (single connection covers all).
Personal data is never written to the brain nor shared between users.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from ..config import Settings
from ..identity import current_context
from .calendar import load_user_creds

_NOT_CONFIGURED = {
    "error": (
        "Integración Google no disponible. "
        "Configurar KAI_GOOGLE_OAUTH_CLIENT_ID y KAI_GOOGLE_OAUTH_CLIENT_SECRET."
    )
}

_SCOPES = {
    "drive":    "https://www.googleapis.com/auth/drive.readonly",
    "contacts": "https://www.googleapis.com/auth/contacts.readonly",
    "tasks":    "https://www.googleapis.com/auth/tasks.readonly",
}


def _has_scope(creds, key: str) -> bool:
    return creds is not None and _SCOPES[key] in (creds.scopes or [])


def _reconnect(oauth_base_url: str) -> dict:
    return {
        "error": "Esta integración no está autorizada todavía (o fue conectada antes de agregarse).",
        "accion": f"Visitá {oauth_base_url}/oauth/google/start?token=<tu-token-kai> para re-autorizar.",
    }


def register_google_personal_tools(mcp: FastMCP, settings: Settings) -> None:

    # ── Drive personal ────────────────────────────────────────────────────────

    @mcp.tool
    async def kai_my_drive_search(query: str, max_results: int = 10) -> dict:
        """Search files in the authenticated user's personal Google Drive.

        Uses Google Drive query syntax:
          - "name contains 'presupuesto'"
          - "mimeType = 'application/vnd.google-apps.document'"
          - "modifiedTime > '2026-01-01'"
          - "fullText contains 'Vaca Muerta'"

        Returns file metadata (name, type, modified date, link).
        Use the file_id with kai_my_drive_read to get the content.

        Note: this reads the user's *personal* Drive, not the shared TBSA Drive
        (use kai_list_drive / kai_read_doc for the shared company Drive).
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if not _has_scope(creds, "drive"):
                return _reconnect(settings.oauth_base_url)

            from googleapiclient.discovery import build

            svc = build("drive", "v3", credentials=creds, cache_discovery=False)

            result = svc.files().list(
                q=query,
                pageSize=min(max_results, 50),
                fields="files(id,name,mimeType,modifiedTime,size,webViewLink,owners)",
            ).execute()

            files = result.get("files", [])
            return {
                "query": query,
                "total": len(files),
                "archivos": [
                    {
                        "id": f["id"],
                        "nombre": f["name"],
                        "tipo": f.get("mimeType", ""),
                        "modificado": f.get("modifiedTime", ""),
                        "link": f.get("webViewLink", ""),
                    }
                    for f in files
                ],
            }

        return await asyncio.to_thread(_fetch)

    @mcp.tool
    async def kai_my_drive_read(file_id: str) -> dict:
        """Read the text content of a file from the authenticated user's personal Google Drive.

        Supports Google Docs, Sheets (as CSV), Slides (as text), and plain text files.
        Returns up to 8000 characters of content.

        Get file_id from kai_my_drive_search results.
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if not _has_scope(creds, "drive"):
                return _reconnect(settings.oauth_base_url)

            from googleapiclient.discovery import build

            svc = build("drive", "v3", credentials=creds, cache_discovery=False)

            meta = svc.files().get(
                fileId=file_id, fields="id,name,mimeType,modifiedTime,webViewLink"
            ).execute()

            mime = meta.get("mimeType", "")

            export_map = {
                "application/vnd.google-apps.document":     "text/plain",
                "application/vnd.google-apps.spreadsheet":  "text/csv",
                "application/vnd.google-apps.presentation": "text/plain",
            }

            if mime in export_map:
                content = svc.files().export(
                    fileId=file_id, mimeType=export_map[mime]
                ).execute()
                text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            elif mime.startswith("text/"):
                content = svc.files().get_media(fileId=file_id).execute()
                text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            else:
                return {
                    "file_id": file_id,
                    "nombre": meta.get("name"),
                    "tipo": mime,
                    "error": "Tipo de archivo no soportado para lectura de texto.",
                }

            return {
                "file_id": file_id,
                "nombre": meta.get("name"),
                "tipo": mime,
                "modificado": meta.get("modifiedTime"),
                "link": meta.get("webViewLink"),
                "contenido": text[:8000],
                "truncado": len(text) > 8000,
            }

        return await asyncio.to_thread(_fetch)

    # ── Contacts ──────────────────────────────────────────────────────────────

    @mcp.tool
    async def kai_my_contacts_search(query: str, max_results: int = 10) -> dict:
        """Search the authenticated user's Google Contacts.

        Searches by name, email, company, or phone number.
        Returns contact details: name, emails, phones, organization, notes.
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if not _has_scope(creds, "contacts"):
                return _reconnect(settings.oauth_base_url)

            from googleapiclient.discovery import build

            svc = build("people", "v1", credentials=creds, cache_discovery=False)

            result = svc.people().searchContacts(
                query=query,
                pageSize=min(max_results, 30),
                readMask="names,emailAddresses,phoneNumbers,organizations,biographies",
            ).execute()

            contacts = []
            for r in result.get("results", []):
                p = r.get("person", {})
                names = p.get("names", [])
                emails = p.get("emailAddresses", [])
                phones = p.get("phoneNumbers", [])
                orgs = p.get("organizations", [])
                bios = p.get("biographies", [])
                contacts.append({
                    "nombre": names[0]["displayName"] if names else "",
                    "emails": [e["value"] for e in emails],
                    "telefonos": [ph["value"] for ph in phones],
                    "empresa": orgs[0]["name"] if orgs else "",
                    "cargo": orgs[0].get("title", "") if orgs else "",
                    "notas": bios[0]["value"][:300] if bios else "",
                })

            return {"query": query, "total": len(contacts), "contactos": contacts}

        return await asyncio.to_thread(_fetch)

    # ── Tasks ─────────────────────────────────────────────────────────────────

    @mcp.tool
    async def kai_my_tasks(tasklist: str = "@default", show_completed: bool = False) -> dict:
        """List tasks from the authenticated user's Google Tasks.

        By default returns pending tasks from the default task list.
        Set show_completed=True to include completed tasks.

        Returns tasks with title, status, due date, and notes.
        """
        if not settings.google_oauth_client_id:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _fetch() -> dict[str, Any]:
            creds = load_user_creds(settings.data_root, ctx.tenant, ctx.user)
            if not _has_scope(creds, "tasks"):
                return _reconnect(settings.oauth_base_url)

            from googleapiclient.discovery import build

            svc = build("tasks", "v1", credentials=creds, cache_discovery=False)

            result = svc.tasks().list(
                tasklist=tasklist,
                showCompleted=show_completed,
                showHidden=False,
                maxResults=50,
            ).execute()

            tasks = result.get("items", [])
            formatted = [
                {
                    "titulo": t.get("title", "(sin título)"),
                    "estado": t.get("status", ""),
                    "vencimiento": t.get("due", ""),
                    "notas": (t.get("notes") or "")[:300],
                    "completado": t.get("completed", ""),
                }
                for t in tasks
            ]

            return {
                "usuario": ctx.user,
                "lista": tasklist,
                "total": len(formatted),
                "tareas": formatted,
            }

        return await asyncio.to_thread(_fetch)
