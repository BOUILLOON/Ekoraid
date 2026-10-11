from __future__ import annotations
import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from fastapi import HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

_rates = OrderedDict()
_lock = threading.Lock()

def install_session(app,cfg,name):
    key = hmac.new(cfg.session_secret.encode(),name.encode(),hashlib.sha256).hexdigest()
    app.add_middleware(SessionMiddleware,secret_key=key,session_cookie=name,max_age=60*60*24*180,same_site="lax",https_only=cfg.cookie_secure)
    @app.middleware("http")
    async def headers(request,call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' https://*.supabase.co; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

def csrf_token(request):
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]

def require_csrf(request):
    expected = request.session.get("csrf", "")
    provided = request.headers.get("X-CSRF-Token", "")
    if not expected or not hmac.compare_digest(str(expected),str(provided)):
        raise HTTPException(403,"Session expirée ou contrôle CSRF absent. Actualisez la page.")

def require_admin(request,cfg):
    if not cfg.admin_password:
        raise HTTPException(503,"Configurez ADMIN_PASSWORD dans .env.local, puis redémarrez.")
    if request.session.get("admin") is not True:
        raise HTTPException(401,"Connexion administrateur nécessaire.")

def rate_limit(request,key,limit,window=60):
    identity = request.session.get("guest_id") or request.session.get("rate_id")
    if not identity:
        identity=secrets.token_urlsafe(20)
        request.session["rate_id"]=identity
    ip=request.client.host if request.client else "local"
    # IP is only a generous abuse bound; it never determines guest identity or votes.
    timestamp=time.monotonic()
    with _lock:
        for scope,maximum in ((f"{key}:{identity}",limit),(f"{key}:ip:{ip}",max(limit*30,300))):
            previous,count = _rates.get(scope,(timestamp,0))
            if timestamp-previous >= window: previous,count=timestamp,0
            if count >= maximum:
                raise HTTPException(429,"Trop de demandes. Patientez une minute puis réessayez.")
            _rates[scope]=(previous,count+1)
            _rates.move_to_end(scope)
        while len(_rates)>20000: _rates.popitem(last=False)
