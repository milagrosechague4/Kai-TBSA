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
from ..identity import current_context

# ---------------------------------------------------------------------------
# Role-based access control for Drive folders
# None = unrestricted (all folders). A set = only those folder IDs (and their
# children one level deep) are accessible. The Shared Drive root is included
# so every role can at least see the top-level structure.
# ---------------------------------------------------------------------------

_DRIVE_ROOT = "0ACfaqd7e8P7ZUk9PVA"

ROLE_FOLDER_ALLOW: dict[str, set[str] | None] = {
    "ceo":                 None,
    "fundador":            None,
    "consultora":          None,
    "directora_operativa": None,
    "dev":                 None,
    "gerente_proyectos": {
        _DRIVE_ROOT,
        "1BFyMrLGIwpx9veiYPK8d_ymRXr2FmmJo",  # 01 - Proyectos
        "1aQNC__PjUQZbTVnmqo2tuKTMAtL_gW43",  # 03 - Comunicación
        "1QioWvqa3B7OlMJxpgZalkspqzb0vewFG",  # 04 - Comercial
        "140n5_QobX9EKaHh_nP55qdGURIagOD6x",  # 05 - Legal
        "1dJ4Bvr8qIrkQxtDn3qKcC2GBsR-rakgH",  # 07 - Institucional
    },
    "office_manager": {
        _DRIVE_ROOT,
        "19FGBjjKL7tlG7f0jBa-oYh9Qo-26MCVV",  # 00 - Admin
        "1cQvykr1NlJkvCHJbfcJ4fwwMYf8QKYzT",  # 02 - Equipo
        "1aQNC__PjUQZbTVnmqo2tuKTMAtL_gW43",  # 03 - Comunicación
        "1dJ4Bvr8qIrkQxtDn3qKcC2GBsR-rakgH",  # 07 - Institucional
        # 06 - Finanzas (Drive Nico) — root + first-level subfolders
        # to cover two levels deep via the one-level-up parent check
        "19f7od2vNW5xvgruO_KD7leQzSEz0ngFj",  # raíz Nico
        "1gXHAoxaXHTUZxypRIzC10jNPKgwFahLg",  # BALANCE TBSA
        "1wG1LAqGHs3vYrg6Z74JbkjtWfu_S4Niq",  # COMPRAS TBSA
        "19KJ4g_2T_5UknmSD35VGRC8lRYZ-zJfV",  # CONCILIACIONES TBSA
        "1wxeW4HyEQikaq_kHSkxS3LT4TxETzYFb",  # EXTRACTOS BANCARIOS TBSA
        "1W--kRV9OLB0zUwvHMhABmAV027moKo8C",  # PETROLERAS
    },
}

_ACCESS_DENIED = {
    "error": "No tenés acceso a esta carpeta.",
    "sugerencia": "Si necesitás esta información, consultá con Lu (coordinadora) o Sebastián.",
}


def _parents_of(file_id: str, drive_svc) -> list[str]:
    """Return the immediate parent folder IDs of a Drive file/folder."""
    try:
        meta = (
            drive_svc.files()
            .get(fileId=file_id, fields="parents", supportsAllDrives=True)
            .execute()
        )
        return meta.get("parents", [])
    except Exception:
        return []


def _is_allowed(role: str | None, folder_id: str, drive_svc) -> bool:
    """Return True if the role can access folder_id or any of its parents."""
    allowed = ROLE_FOLDER_ALLOW.get(role or "")
    if allowed is None:
        return True
    if folder_id in allowed:
        return True
    # Check one level up — covers subfolders of allowed top-level folders.
    for parent in _parents_of(folder_id, drive_svc):
        if parent in allowed:
            return True
    return False


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

        ctx = current_context(settings)

        def _read() -> dict[str, Any]:
            drive_svc, sheets_svc = _build_services(sa_json)

            # Check that the sheet lives in a folder the caller can access.
            if not _is_allowed(ctx.role, sheet_id, drive_svc):
                return _ACCESS_DENIED

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

        ctx = current_context(settings)

        def _list() -> dict[str, Any]:
            drive_svc, _ = _build_services(sa_json)

            if not _is_allowed(ctx.role, folder_id, drive_svc):
                return _ACCESS_DENIED

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

    @mcp.tool
    async def kai_read_doc(doc_id: str) -> dict:
        """Read the text content of a Google Doc from the company Drive.

        doc_id: the alphanumeric ID from the Google Doc URL (the part after /d/).
        Returns the document text as plain text. Only works with Google Docs
        (not PDFs, Sheets, or other file types — use kai_read_sheet for Sheets).
        Use kai_list_drive to find document IDs.
        """
        if not sa_json:
            return _NOT_CONFIGURED

        ctx = current_context(settings)

        def _read_doc() -> dict[str, Any]:
            drive_svc, _ = _build_services(sa_json)

            if not _is_allowed(ctx.role, doc_id, drive_svc):
                return _ACCESS_DENIED

            try:
                meta = (
                    drive_svc.files()
                    .get(fileId=doc_id, fields="name,mimeType", supportsAllDrives=True)
                    .execute()
                )
            except Exception as e:
                return {"error": f"No se pudo acceder al documento: {e}"}

            mime = meta.get("mimeType", "")

            if mime == "application/vnd.google-apps.document":
                # Google Doc nativo → exportar como texto plano
                try:
                    content = (
                        drive_svc.files()
                        .export(fileId=doc_id, mimeType="text/plain")
                        .execute()
                    )
                    text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
                except Exception as e:
                    return {"error": f"Error exportando el documento: {e}"}

            elif mime == "application/pdf":
                # PDF binario → descargar y parsear con pypdf
                try:
                    import io
                    from pypdf import PdfReader

                    request = drive_svc.files().get_media(fileId=doc_id, supportsAllDrives=True)
                    pdf_bytes = io.BytesIO(request.execute())
                    reader = PdfReader(pdf_bytes)
                    pages = [page.extract_text() or "" for page in reader.pages]
                    text = "\n\n".join(pages).strip()
                    if not text:
                        return {"error": "El PDF no tiene texto extraíble (puede ser imagen escaneada)."}
                except Exception as e:
                    return {"error": f"Error parseando el PDF: {e}"}

            else:
                return {
                    "error": f"Tipo de archivo no soportado ({mime}). kai_read_doc lee Google Docs y PDFs. Usá kai_read_sheet para Sheets."
                }

            return {
                "doc_id": doc_id,
                "name": meta.get("name", ""),
                "characters": len(text),
                "content": text,
            }

        return await asyncio.to_thread(_read_doc)
