"""Google Drive + Sheets read-only tools for Kai.

Requires KAI_GOOGLE_SA_JSON (env var on Railway) set to the full contents
of a Google service account key JSON file. The service account must be shared
as a Viewer on the company Shared Drive.

If the env var is absent the tools register normally but return a clear error
message — the server still starts and all other tools work.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastmcp import FastMCP

from ..config import Settings

_NOT_CONFIGURED = {
    "error": (
        "Google Drive no configurado. "
        "Agregar KAI_GOOGLE_SA_JSON como variable de entorno en Railway "
        "con el contenido del JSON de la Service Account."
    )
}

_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


def _build_services(sa_json: str):
    """Build Drive + Sheets API clients from service account JSON string."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=_SCOPES
    )
    drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets_svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return drive_svc, sheets_svc


def register_drive_tools(mcp: FastMCP, settings: Settings) -> None:
    sa_json = settings.google_sa_json

    @mcp.tool
    async def kai_read_sheet(sheet_id: str, tab: str = "") -> dict:
        """Read a Google Sheet from the company Drive and return it as a markdown table.

        sheet_id: the alphanumeric ID from the Sheets URL (the part after /d/).
        tab: sheet tab name to read. If omitted, reads the first tab.
        Returns the sheet as a markdown table plus metadata (tab name, row count,
        available tabs). Use this to answer questions about press coverage,
        project metrics, or any structured data stored in company Sheets.
        """
        if not sa_json:
            return _NOT_CONFIGURED

        def _read() -> dict[str, Any]:
            _, sheets_svc = _build_services(sa_json)

            meta = sheets_svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
            available_tabs = [s["properties"]["title"] for s in meta["sheets"]]
            target_tab = tab if tab else available_tabs[0]

            result = (
                sheets_svc.spreadsheets()
                .values()
                .get(spreadsheetId=sheet_id, range=target_tab)
                .execute()
            )
            rows: list[list[str]] = result.get("values", [])
            if not rows:
                return {
                    "tab": target_tab,
                    "available_tabs": available_tabs,
                    "rows": 0,
                    "content": "(hoja vacía)",
                }

            header = rows[0]
            separator = ["---"] * len(header)
            body = rows[1:]

            def _row(cells: list[str]) -> str:
                padded = list(cells) + [""] * max(0, len(header) - len(cells))
                return "| " + " | ".join(str(c) for c in padded) + " |"

            lines = [_row(header), _row(separator)] + [_row(r) for r in body]
            return {
                "tab": target_tab,
                "available_tabs": available_tabs,
                "rows": len(body),
                "content": "\n".join(lines),
            }

        return await asyncio.to_thread(_read)

    @mcp.tool
    async def kai_list_drive(folder_id: str = "0ACfaqd7e8P7ZUk9PVA") -> dict:
        """List files and folders in the company Google Drive.

        folder_id: Drive folder ID or Shared Drive root ID.
        Defaults to the company Shared Drive root.
        Returns name, ID, type (file/folder), and URL for each entry.
        Use this to navigate the Drive structure and find sheet IDs.
        """
        if not sa_json:
            return _NOT_CONFIGURED

        def _list() -> dict[str, Any]:
            drive_svc, _ = _build_services(sa_json)
            results = (
                drive_svc.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="files(id, name, mimeType, webViewLink)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    orderBy="name",
                )
                .execute()
            )
            files = results.get("files", [])
            entries = [
                {
                    "name": f["name"],
                    "id": f["id"],
                    "type": "folder"
                    if f["mimeType"] == "application/vnd.google-apps.folder"
                    else "file",
                    "url": f.get("webViewLink", ""),
                }
                for f in files
            ]
            return {"folder_id": folder_id, "count": len(entries), "entries": entries}

        return await asyncio.to_thread(_list)
