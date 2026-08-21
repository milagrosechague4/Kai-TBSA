"""ChatGPT plugin layer — REST API + manifest + OpenAPI spec.

Exposes the same kai_* operations as JSON REST endpoints so ChatGPT's
Plugins / Connectors feature can call them. Auth uses the same static bearer
token whitelist — each team member installs the plugin with their personal token.

Also compatible with Claude.ai web connectors and any OpenAPI-capable client.
The TokenInPathMiddleware already handles /c/<token>/api/... → /api/... + header,
so URL-embedded tokens work here too (useful for one-URL-per-person installs).

Routes (all require Authorization: Bearer <token>):
  GET  /.well-known/ai-plugin.json   → ChatGPT plugin manifest
  GET  /logo.svg                     → Plugin logo
  GET  /openapi.json                 → OpenAPI 3.1 spec
  GET  /api/me                       → Caller identity + profile
  GET  /api/list?folder=.            → List files in a folder
  GET  /api/read?path=...            → Read a file (+ optional offset/limit)
  PUT  /api/write?path=...           → Create or update a file
  PATCH /api/edit?path=...           → Replace exact text in a file
  DELETE /api/delete?path=...        → Delete a file
  GET  /api/search?q=...             → Full-text search across the brain
  GET  /api/history?path=...         → Commit history for a file
"""

from __future__ import annotations

import asyncio
import json

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import fs
from ..auth import resolve_caller
from ..config import Settings
from ..git import CommitContext
from ..locks import tenant_lock

# ── Plugin metadata ──────────────────────────────────────────────────────────

_SERVER_URL = "https://stunning-spontaneity-production-4cbd.up.railway.app"

_DESCRIPTION_FOR_MODEL = """\
Kai es el cerebro institucional de TBSA — el ecosistema de empresas que construye \
el sistema logístico de Vaca Muerta: Distrito Energético, Corredor Ferroviario \
Norpatagónico (TREN TBSA CARGO), y Puerto Punta Colorada.

PROTOCOLO DE USO:
1. Empezar cada sesión con whoAmI para confirmar identidad del usuario.
2. Usar listFiles para ver qué archivos existen (empezar por la raíz ".").
3. Leer el archivo relevante con readFile.
4. Si no sabés en qué archivo está la info, usar searchBrain con palabras clave.

CONTRATO DE RESPUESTA:
- Toda afirmación material cita el archivo fuente y la fecha del dato.
- Si la información no está en el brain, declararlo explícitamente. Nunca inventar.
- Las inferencias se etiquetan como tales — nunca se presentan como hechos.
- Incluir confianza y gaps cuando sean relevantes.

ESCRITURA:
- Solo llamar writeFile, editFile o deleteFile cuando el usuario lo pide explícitamente.
- Confirmar con el usuario qué se actualiza antes de escribir.

RESTRICCIONES:
- No compartir datos financieros confidenciales (TIR, EBITDA, nominaciones de inversores).
- No atribuir compromisos ni decisiones que no estén documentados en el brain.
- No ejecutar escrituras sin pedido explícito.
"""

_PLUGIN_MANIFEST = {
    "schema_version": "v1",
    "name_for_model": "kai_tbsa",
    "name_for_human": "Kai — Cerebro TBSA",
    "description_for_human": (
        "Consultá el cerebro institucional de TBSA: proyectos del Distrito Energético, "
        "equipo, decisiones estratégicas y estado operativo de Vaca Muerta."
    ),
    "description_for_model": _DESCRIPTION_FOR_MODEL,
    "auth": {
        "type": "service_http",
        "authorization_type": "bearer",
    },
    "api": {
        "type": "openapi",
        "url": f"{_SERVER_URL}/openapi.json",
    },
    "logo_url": f"{_SERVER_URL}/logo.svg",
    "contact_email": "mila@beyondgpt.io",
    "legal_info_url": _SERVER_URL,
}

_OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Kai Brain API — TBSA",
        "description": (
            "Cerebro institucional de TBSA. Acceso a proyectos, equipo, decisiones "
            "y contexto operativo del Distrito Energético / Vaca Muerta."
        ),
        "version": "0.1.0",
    },
    "servers": [{"url": _SERVER_URL}],
    "security": [{"BearerAuth": []}],
    "components": {
        "securitySchemes": {
            "BearerAuth": {"type": "http", "scheme": "bearer"}
        }
    },
    "paths": {
        "/api/me": {
            "get": {
                "operationId": "whoAmI",
                "summary": "Identidad del usuario",
                "description": (
                    "Llamar primero en cada sesión. Devuelve el tenant, usuario, rol "
                    "y perfil del caller. Usar para confirmar que la conexión funciona."
                ),
                "responses": {"200": {"description": "Identidad del caller"}},
            }
        },
        "/api/list": {
            "get": {
                "operationId": "listFiles",
                "summary": "Listar archivos del brain",
                "description": (
                    "Lista archivos y subcarpetas. Empezar con folder='.' para ver la "
                    "estructura completa. Luego navegar a subcarpetas (ej: 'ecosistema')."
                ),
                "parameters": [
                    {
                        "name": "folder",
                        "in": "query",
                        "description": "Carpeta a listar. Default: raíz ('.'). Ej: 'ecosistema'",
                        "schema": {"type": "string", "default": "."},
                    }
                ],
                "responses": {"200": {"description": "Lista de archivos y subcarpetas"}},
            }
        },
        "/api/read": {
            "get": {
                "operationId": "readFile",
                "summary": "Leer un archivo del brain",
                "description": (
                    "Lee el contenido de un archivo. Para archivos largos, usar offset "
                    "(línea de inicio, 1-based) y limit (máximo de líneas) para paginar. "
                    "La respuesta incluye total_lines para saber si hay más."
                ),
                "parameters": [
                    {
                        "name": "path",
                        "in": "query",
                        "required": True,
                        "description": "Ruta relativa. Ej: 'ecosistema/distrito-energetico.md'",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "offset",
                        "in": "query",
                        "description": "Línea de inicio (1-based; 0 = desde el principio)",
                        "schema": {"type": "integer", "default": 0},
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "description": "Máximo de líneas a devolver (0 = hasta el final)",
                        "schema": {"type": "integer", "default": 0},
                    },
                ],
                "responses": {"200": {"description": "Contenido del archivo"}},
            }
        },
        "/api/write": {
            "put": {
                "operationId": "writeFile",
                "summary": "Crear o actualizar un archivo",
                "description": (
                    "Solo usar cuando el usuario lo pide explícitamente. "
                    "mode='overwrite' reemplaza el archivo completo; "
                    "mode='append' agrega contenido al final."
                ),
                "parameters": [
                    {
                        "name": "path",
                        "in": "query",
                        "required": True,
                        "description": "Ruta del archivo. Ej: 'reuniones/2026-08-21.md'",
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "string",
                                        "description": "Contenido completo del archivo",
                                    },
                                    "mode": {
                                        "type": "string",
                                        "enum": ["overwrite", "append"],
                                        "default": "overwrite",
                                    },
                                },
                                "required": ["content"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Resultado de la escritura"}},
            }
        },
        "/api/edit": {
            "patch": {
                "operationId": "editFile",
                "summary": "Reemplazar texto exacto en un archivo",
                "description": (
                    "Edición quirúrgica: solo cambia old_string → new_string sin "
                    "reescribir el archivo. old_string debe ser único en el archivo — "
                    "agregar contexto si no lo es, o usar replace_all=true."
                ),
                "parameters": [
                    {
                        "name": "path",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "old_string": {"type": "string"},
                                    "new_string": {"type": "string"},
                                    "replace_all": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": "Reemplazar todas las ocurrencias",
                                    },
                                },
                                "required": ["old_string", "new_string"],
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Resultado de la edición"}},
            }
        },
        "/api/delete": {
            "delete": {
                "operationId": "deleteFile",
                "summary": "Eliminar un archivo del brain",
                "description": "Irreversible. Confirmar el path con listFiles antes de llamar.",
                "parameters": [
                    {
                        "name": "path",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Confirmación de eliminación"}},
            }
        },
        "/api/search": {
            "get": {
                "operationId": "searchBrain",
                "summary": "Buscar en el brain por contenido",
                "description": (
                    "Búsqueda por palabras clave en todos los archivos. Todos los "
                    "términos deben aparecer en el archivo. Útil cuando no sabés "
                    "en qué archivo está la información."
                ),
                "parameters": [
                    {
                        "name": "q",
                        "in": "query",
                        "required": True,
                        "description": "Términos de búsqueda. Ej: 'ferroviario capex' o 'Gustavo PM'",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer", "default": 20},
                    },
                ],
                "responses": {
                    "200": {"description": "Archivos que contienen los términos, con puntaje y snippets"}
                },
            }
        },
        "/api/history": {
            "get": {
                "operationId": "fileHistory",
                "summary": "Historial de cambios de un archivo",
                "description": "Muestra quién modificó el archivo, cuándo y con qué mensaje. Útil para ver evolución de decisiones.",
                "parameters": [
                    {
                        "name": "path",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer", "default": 20},
                    },
                ],
                "responses": {"200": {"description": "Commits que modificaron el archivo"}},
            }
        },
    },
}

_LOGO_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <rect width="64" height="64" rx="12" fill="#0f172a"/>
  <text x="32" y="44" font-family="monospace" font-size="28" font-weight="bold"
        text-anchor="middle" fill="#38bdf8">K</text>
</svg>"""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


# ── Route registration ────────────────────────────────────────────────────────


def register_plugin(mcp: FastMCP, settings: Settings) -> None:
    """Register the ChatGPT plugin manifest, OpenAPI spec, and REST endpoints."""

    # ── Static assets (no auth required) ─────────────────────────────────────

    @mcp.custom_route("/.well-known/ai-plugin.json", methods=["GET"])
    async def plugin_manifest(_: Request) -> Response:
        return Response(
            content=json.dumps(_PLUGIN_MANIFEST, ensure_ascii=False, indent=2),
            media_type="application/json",
        )

    @mcp.custom_route("/logo.svg", methods=["GET"])
    async def logo(_: Request) -> Response:
        return Response(content=_LOGO_SVG, media_type="image/svg+xml")

    @mcp.custom_route("/openapi.json", methods=["GET"])
    async def openapi_spec(_: Request) -> Response:
        return Response(
            content=json.dumps(_OPENAPI_SPEC, ensure_ascii=False, indent=2),
            media_type="application/json",
        )

    # ── Identity ──────────────────────────────────────────────────────────────

    @mcp.custom_route("/api/me", methods=["GET"])
    async def api_me(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        profile = None
        try:
            profile = fs.read_file(settings, root, f"_identity/{ctx.user}.md")["content"]
        except fs.PathError:
            pass
        return JSONResponse({"tenant": ctx.tenant, "user": ctx.user, "role": ctx.role, "profile": profile})

    # ── Read operations ───────────────────────────────────────────────────────

    @mcp.custom_route("/api/list", methods=["GET"])
    async def api_list(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        folder = request.query_params.get("folder", ".")
        try:
            return JSONResponse(fs.list_tree(settings, root, folder))
        except fs.PathError as exc:
            return _err(str(exc))

    @mcp.custom_route("/api/read", methods=["GET"])
    async def api_read(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        path = request.query_params.get("path", "")
        if not path:
            return _err("path is required")
        try:
            offset = int(request.query_params.get("offset", "0"))
            limit = int(request.query_params.get("limit", "0"))
        except ValueError:
            return _err("offset and limit must be integers")
        try:
            return JSONResponse(fs.read_file(settings, root, path, offset, limit))
        except fs.PathError as exc:
            return _err(str(exc), 404)

    @mcp.custom_route("/api/search", methods=["GET"])
    async def api_search(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        q = request.query_params.get("q", "")
        if not q:
            return _err("q is required")
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            return _err("limit must be an integer")
        limit = max(1, min(limit, 100))
        return JSONResponse(fs.search(settings, root, q, limit))

    @mcp.custom_route("/api/history", methods=["GET"])
    async def api_history(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        path = request.query_params.get("path", "")
        if not path:
            return _err("path is required")
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            return _err("limit must be an integer")
        limit = max(1, min(limit, 100))
        try:
            return JSONResponse(fs.file_history(settings, root, path, limit))
        except fs.PathError as exc:
            return _err(str(exc))

    # ── Write operations ──────────────────────────────────────────────────────

    @mcp.custom_route("/api/write", methods=["PUT"])
    async def api_write(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        path = request.query_params.get("path", "")
        if not path:
            return _err("path is required")
        try:
            body = await request.json()
        except Exception:
            return _err("invalid JSON body")
        content = body.get("content")
        if content is None:
            return _err("content is required")
        mode = body.get("mode", "overwrite")
        cc = CommitContext(user=ctx.user, role=ctx.role or "", tool="plugin:write")
        try:
            async with tenant_lock(root):
                result = await asyncio.to_thread(
                    fs.write_file, settings, root, path, content, mode, cc
                )
            return JSONResponse(result)
        except fs.PathError as exc:
            return _err(str(exc))

    @mcp.custom_route("/api/edit", methods=["PATCH"])
    async def api_edit(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        path = request.query_params.get("path", "")
        if not path:
            return _err("path is required")
        try:
            body = await request.json()
        except Exception:
            return _err("invalid JSON body")
        old_str = body.get("old_string")
        new_str = body.get("new_string")
        if old_str is None or new_str is None:
            return _err("old_string and new_string are required")
        replace_all = bool(body.get("replace_all", False))
        cc = CommitContext(user=ctx.user, role=ctx.role or "", tool="plugin:edit")
        try:
            async with tenant_lock(root):
                result = await asyncio.to_thread(
                    fs.edit_file, settings, root, path, old_str, new_str, replace_all, cc
                )
            return JSONResponse(result)
        except fs.PathError as exc:
            return _err(str(exc))

    @mcp.custom_route("/api/delete", methods=["DELETE"])
    async def api_delete(request: Request) -> JSONResponse:
        ctx, root, err = resolve_caller(settings, request)
        if err:
            return err
        path = request.query_params.get("path", "")
        if not path:
            return _err("path is required")
        cc = CommitContext(user=ctx.user, role=ctx.role or "", tool="plugin:delete")
        try:
            async with tenant_lock(root):
                result = await asyncio.to_thread(fs.delete_file, settings, root, path, cc)
            return JSONResponse(result)
        except fs.PathError as exc:
            return _err(str(exc), 404)
