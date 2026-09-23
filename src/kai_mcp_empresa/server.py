"""FastMCP server assembly. Streamable HTTP transport, fail-closed auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from .auth import build_auth
from .config import Settings, get_settings
from .fs import sweep_stale_temps
from .obs import ToolCallLogger
from .tools.airtable import register_airtable_tools
from .tools.calendar import CALENDAR_SCOPES, register_calendar_tools, save_user_creds
from .tools.gmail import register_gmail_tools
from .tools.google_personal import register_google_personal_tools
from .tools.drive import register_drive_tools
from .tools.files import register_tools
from .tools.plugin import register_plugin

# ── OAuth state helpers ──────────────────────────────────────────────────────
# State encodes tenant+user, signed with the OAuth client secret so the
# callback cannot be forged. Valid for 10 minutes (browser round-trip).

_STATE_TTL_S = 600


def _make_state(tenant: str, user: str, client_secret: str, code_verifier: str | None = None) -> str:
    data: dict = {"t": tenant, "u": user, "ts": int(time.time())}
    if code_verifier:
        data["cv"] = code_verifier
    payload = base64.urlsafe_b64encode(
        json.dumps(data).encode()
    ).decode().rstrip("=")
    sig = hmac.new(client_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{payload}.{sig}"


def _verify_state(state: str, client_secret: str) -> dict | None:
    try:
        payload, sig = state.rsplit(".", 1)
        expected = hmac.new(client_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
        if not hmac.compare_digest(sig, expected):
            return None
        padding = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
        if int(time.time()) - data["ts"] > _STATE_TTL_S:
            return None
        return data
    except Exception:
        return None


# ── Bearer-in-URL middleware ─────────────────────────────────────────────────
# Allows Claude connectors (and any HTTP client) to pass the Kai token as a
# query param instead of an Authorization header:
#   /mcp?bearer=kai_tbsa_xxx
# The middleware promotes it to "Authorization: Bearer …" before FastMCP auth
# sees the request, so no changes are needed elsewhere.

class BearerFromQueryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        bearer = request.query_params.get("bearer")
        if bearer and "authorization" not in request.headers:
            headers = dict(request.headers)
            headers["authorization"] = f"Bearer {bearer}"
            request._headers = request.headers.__class__(
                scope=request.scope,
                headers=[(k.encode(), v.encode()) for k, v in headers.items()],
            )
        return await call_next(request)


# ── Server factory ───────────────────────────────────────────────────────────

def build_server(settings: Settings | None = None) -> tuple[FastMCP, Settings]:
    settings = settings or get_settings()
    settings.validate_fail_closed()

    mcp = FastMCP(name="kai-mcp-empresa", auth=build_auth(settings))
    mcp.add_middleware(BearerFromQueryMiddleware)
    register_tools(mcp, settings)
    register_drive_tools(mcp, settings)
    register_airtable_tools(mcp, settings)
    register_calendar_tools(mcp, settings)
    register_gmail_tools(mcp, settings)
    register_google_personal_tools(mcp, settings)
    register_plugin(mcp, settings)

    if settings.log_toolcalls:
        mcp.add_middleware(ToolCallLogger(settings))

    sweep_stale_temps(settings.data_root)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # ── OAuth: step 1 — redirect user to Google consent screen ──────────────
    # The user visits this URL in their browser with their Kai token as a query
    # param (bearer header is not available in a browser redirect).
    # Example: /oauth/google/start?token=kai_tbsa_abc123
    @mcp.custom_route("/oauth/google/start", methods=["GET"])
    async def oauth_start(request: Request) -> HTMLResponse | RedirectResponse:
        if not settings.google_oauth_client_id:
            return HTMLResponse("<h2>Google Calendar no configurado en el servidor.</h2>", status_code=503)

        token = request.query_params.get("token", "").strip()
        if not token:
            return HTMLResponse("<h2>Falta el parámetro ?token=</h2>", status_code=400)

        # Verify the Kai token and extract identity.
        from .auth import _tokens_for
        from .identity import ForbiddenError, context_from_claims

        try:
            tokens = _tokens_for(settings)
            if token not in tokens:
                return HTMLResponse("<h2>Token inválido.</h2>", status_code=401)
            ctx = context_from_claims(tokens[token])
        except ForbiddenError as exc:
            return HTMLResponse(f"<h2>Error de autenticación: {exc}</h2>", status_code=403)

        from google_auth_oauthlib.flow import Flow

        redirect_uri = f"{settings.oauth_base_url}/oauth/google/callback"
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "redirect_uris": [redirect_uri],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=CALENDAR_SCOPES,
            redirect_uri=redirect_uri,
        )

        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        state = _make_state(ctx.tenant, ctx.user, settings.google_oauth_client_secret, code_verifier)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
            code_challenge=code_challenge,
            code_challenge_method="S256",
        )
        return RedirectResponse(auth_url)

    # ── OAuth: step 2 — Google redirects back here with the auth code ────────
    @mcp.custom_route("/oauth/google/callback", methods=["GET"])
    async def oauth_callback(request: Request) -> HTMLResponse:
        if not settings.google_oauth_client_id:
            return HTMLResponse("<h2>Google Calendar no configurado.</h2>", status_code=503)

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        error = request.query_params.get("error", "")

        if error:
            return HTMLResponse(
                f"<h2>Autorización cancelada.</h2><p>Google respondió: {error}</p>",
                status_code=400,
            )
        if not code or not state:
            return HTMLResponse("<h2>Parámetros faltantes en el callback.</h2>", status_code=400)

        ctx_data = _verify_state(state, settings.google_oauth_client_secret)
        if not ctx_data:
            return HTMLResponse(
                "<h2>State inválido o expirado.</h2>"
                "<p>El link de conexión dura 10 minutos. Volvé a /oauth/google/start y probá de nuevo.</p>",
                status_code=400,
            )

        tenant, user = ctx_data["t"], ctx_data["u"]
        code_verifier = ctx_data.get("cv")

        from google_auth_oauthlib.flow import Flow

        redirect_uri = f"{settings.oauth_base_url}/oauth/google/callback"
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "redirect_uris": [redirect_uri],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=CALENDAR_SCOPES,
            redirect_uri=redirect_uri,
            state=state,
        )

        try:
            flow.fetch_token(code=code, code_verifier=code_verifier)
        except Exception as exc:
            return HTMLResponse(
                f"<h2>Error intercambiando el código de autorización.</h2><p>{exc}</p>",
                status_code=500,
            )

        # ── Domain restriction ────────────────────────────────────────────────
        # If KAI_OAUTH_ALLOWED_DOMAIN is set (e.g. "tbsa.ar"), reject any Google
        # account that doesn't belong to that domain before saving credentials.
        if settings.oauth_allowed_domain:
            try:
                from googleapiclient.discovery import build as _build
                userinfo_svc = _build("oauth2", "v2", credentials=flow.credentials, cache_discovery=False)
                user_info = userinfo_svc.userinfo().get().execute()
                email: str = user_info.get("email", "").lower()
                allowed = settings.oauth_allowed_domain.lower().lstrip("@")
                if not email.endswith(f"@{allowed}"):
                    return HTMLResponse(
                        f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Kai Brain · Cuenta no autorizada</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; background: #101820; color: #d8e2ea;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .box {{ text-align: center; max-width: 440px; padding: 40px; }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    h1 {{ font-size: 20px; font-weight: 700; color: #fff; margin: 0 0 10px; }}
    p {{ font-size: 14px; color: #6b7c8f; line-height: 1.6; margin: 0 0 8px; }}
    .email {{ color: #C41230; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="icon">✗</div>
    <h1>Cuenta no autorizada</h1>
    <p>Solo se pueden conectar cuentas <span class="email">@{allowed}</span>.</p>
    <p>Intentaste con <span class="email">{email}</span>.</p>
    <p style="margin-top:16px;">Volvé a intentarlo con tu cuenta de TBSA.</p>
  </div>
</body>
</html>""",
                        status_code=403,
                    )
            except Exception as exc:
                return HTMLResponse(
                    f"<h2>No se pudo verificar el dominio de la cuenta.</h2><p>{exc}</p>",
                    status_code=500,
                )

        save_user_creds(
            settings.data_root,
            tenant,
            user,
            flow.credentials,
            settings.google_oauth_client_id,
            settings.google_oauth_client_secret,
        )

        return HTMLResponse(
            f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kai Brain · Google conectado</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; background: #101820; color: #d8e2ea;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .box {{ text-align: center; max-width: 480px; padding: 40px; }}
    .check {{ font-size: 48px; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; font-weight: 700; color: #fff; margin: 0 0 12px; }}
    .chips {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin: 16px 0 20px; }}
    .chip {{ font-size: 12px; font-weight: 600; background: #1b2636; border: 1px solid #243545;
             color: #8899aa; padding: 4px 10px; border-radius: 4px; }}
    p {{ font-size: 14px; color: #6b7c8f; line-height: 1.6; margin: 0; }}
    .user {{ color: #C41230; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="check">✓</div>
    <h1>Google conectado</h1>
    <div class="chips">
      <span class="chip">Calendar</span>
      <span class="chip">Gmail</span>
      <span class="chip">Drive</span>
      <span class="chip">Contacts</span>
      <span class="chip">Tasks</span>
    </div>
    <p>Ya podés usar <strong>@Kai Brain TBSA</strong> en ChatGPT.<br>
    Conectado como <span class="user">{user}</span> en el tenant <span class="user">{tenant}</span>.</p>
  </div>
</body>
</html>"""
        )

    return mcp, settings
