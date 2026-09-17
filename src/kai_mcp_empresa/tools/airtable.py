"""Airtable read-only tools for Kai.

Provides kai_query_airtable — queries the TBSA Airtable base for content
scheduling (Calendario de Contenido) and LinkedIn lead tracking
(LinkedIn Mensajes Sebastián).

Requires KAI_AIRTABLE_TOKEN (env var on Railway) set to a Personal Access
Token with data.records:read scope on base appA00Nc1qVXa1lar.
If absent, tools register normally but return a clear error.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from typing import Any

from fastmcp import FastMCP

from ..config import Settings

# ---------------------------------------------------------------------------
# Table registry
# ---------------------------------------------------------------------------

_BASE_ID = "appA00Nc1qVXa1lar"
_API_BASE = "https://api.airtable.com/v0"

_TABLES: dict[str, dict[str, str]] = {
    "calendario": {
        "id": "tblqs01I3tF8iH5jY",
        "name": "Calendario de Contenido",
        "description": "Posts LinkedIn programados — estado, fechas, canal, copy.",
        "default_sort_field": "Fecha",
        "default_sort_direction": "asc",
    },
    "leads": {
        "id": "tbl44j7eMg5SejBxJ",
        "name": "LinkedIn Mensajes Sebastián",
        "description": "CRM de mensajes y contactos entrantes de LinkedIn — tipo, prioridad, estado.",
        "default_sort_field": "",
        "default_sort_direction": "desc",
    },
}

_NOT_CONFIGURED = {
    "error": (
        "Airtable no configurado. "
        "Agregar KAI_AIRTABLE_TOKEN como variable de entorno en Railway "
        "con un Personal Access Token con scope data.records:read."
    )
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str:
    """Convert any Airtable field value to a clean string."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_stringify(v) for v in value)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or value)
    return str(value)


def _to_markdown(records: list[dict]) -> str:
    """Convert Airtable records list to a markdown table."""
    if not records:
        return "(sin registros)"

    seen: dict[str, None] = {}
    for record in records:
        for k in record.get("fields", {}):
            seen[k] = None
    headers = list(seen.keys())
    if not headers:
        return "(registros sin campos)"

    def _row(cells: list[str]) -> str:
        cleaned = [str(c).replace("\n", " ").replace("|", r"\|") for c in cells]
        return "| " + " | ".join(cleaned) + " |"

    lines = [_row(headers), _row(["---"] * len(headers))]
    for rec in records:
        fields = rec.get("fields", {})
        lines.append(_row([_stringify(fields.get(h)) for h in headers]))
    return "\n".join(lines)


def _fetch(token: str, table_info: dict, formula: str, limit: int) -> dict[str, Any]:
    """Synchronous Airtable REST fetch (run via asyncio.to_thread)."""
    table_id = table_info["id"]
    params: dict[str, Any] = {
        "maxRecords": limit,
        "pageSize": min(limit, 100),
    }
    if formula:
        params["filterByFormula"] = formula

    sort_field = table_info.get("default_sort_field", "")
    sort_dir = table_info.get("default_sort_direction", "asc")
    if sort_field:
        params["sort[0][field]"] = sort_field
        params["sort[0][direction]"] = sort_dir
    else:
        params["sort[0][field]"] = "createdTime"
        params["sort[0][direction]"] = sort_dir

    url = f"{_API_BASE}/{_BASE_ID}/{table_id}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"Airtable HTTP {e.code}: {body[:300]}"}
    except Exception as exc:
        return {"error": f"Error de red: {exc}"}

    records = data.get("records", [])
    return {
        "table": table_info["name"],
        "count": len(records),
        "content": _to_markdown(records),
    }


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_airtable_tools(mcp: FastMCP, settings: Settings) -> None:
    token = settings.airtable_token

    @mcp.tool
    async def kai_query_airtable(
        table: str,
        filter_formula: str = "",
        limit: int = 50,
    ) -> dict:
        """Query the TBSA Airtable base and return records as a markdown table.

        table: which table to query. Options:
          - "calendario" -> Calendario de Contenido (posts LinkedIn: estado, fecha, canal, copy)
          - "leads"      -> LinkedIn Mensajes Sebastian (contactos: tipo, prioridad, estado, notas)

        filter_formula: optional Airtable formula to filter records. Examples:
          - '{Estado} = "Aprobado"'
          - '{Estado} = "Publicado"'
          - '{Tipo} = "Socio potencial"'
          - 'AND({Estado} = "Entrante", {Prioridad} = "Alta")'
          Leave empty to return all records.

        limit: max records to return (default 50, max 100).

        Use this to answer questions like:
          - "Que posts estan programados esta semana?" -> table="calendario"
          - "Que posts estan pendientes de aprobacion?" -> table="calendario",
            filter_formula='{Estado}="Enviado a aprobacion"'
          - "Cual es el estado del post de Mendoza?" -> table="calendario"
          - "Alguien de infraestructura me escribio en LinkedIn?" -> table="leads"
          - "Que leads estan sin respuesta?" -> table="leads",
            filter_formula='{Estado}="Entrante"'
        """
        if not token:
            return _NOT_CONFIGURED

        key = table.lower().strip()
        if key not in _TABLES:
            opts = ", ".join('"' + k + '"' for k in _TABLES)
            return {"error": f"Tabla '{table}' no reconocida. Opciones: {opts}."}

        limit = max(1, min(limit, 100))
        return await asyncio.to_thread(_fetch, token, _TABLES[key], filter_formula, limit)
