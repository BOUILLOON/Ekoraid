"""Private endpoints for a public, separately hosted collection."""
from contextlib import closing
import threading
import io
from typing import Literal
from urllib.parse import urlsplit
import qrcode

from fastapi import HTTPException, Request
from fastapi.responses import Response
from . import db
from .security import require_admin, require_csrf, rate_limit

_lock=threading.Lock()

def add_cloud_routes(app,cfg):
    @app.get('/api/qr')
    def qr(request:Request,scope:Literal['local','public']='local'):
        require_admin(request,cfg)
        with closing(db.connect(cfg)) as conn:
            url=db.get_setting(conn,f'{scope}_url',getattr(cfg,f'{scope}_url'))
        parts=urlsplit(url)
        if len(url)>2048 or parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password:
            raise HTTPException(400,'Configurez une adresse valide dans les réglages avant de générer son QR code.')
        output=io.BytesIO()
        qrcode.make(url).save(output,format='PNG')
        return Response(output.getvalue(),media_type='image/png')

    @app.get('/api/cloud/status')
    def status(request:Request):
        require_admin(request,cfg)
        with closing(db.connect(cfg)) as conn:
            return {'configured':bool(cfg.supabase_url and cfg.supabase_service_role_key),
                    'url':cfg.supabase_url,'secret_present':bool(cfg.supabase_service_role_key),
                    'last_sync':db.get_setting(conn,'cloud_last_sync',None),
                    'offline':db.get_setting(conn,'offline_mode',False),
                    'message':'Renseignez SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY dans .env.local sur votre PC, puis redémarrez.' if not(cfg.supabase_url and cfg.supabase_service_role_key) else 'La collecte publique fonctionne indépendamment de votre PC.'}

    def operation(request,kind):
        require_admin(request,cfg); require_csrf(request)
        rate_limit(request,'cloud_sync',10)
        if not _lock.acquire(blocking=False):
            raise HTTPException(409,'Une synchronisation est déjà en cours.')
        try:
            from . import cloud_sync
            with closing(db.connect(cfg)) as conn:
                with conn:
                    if db.get_setting(conn,'offline_mode',False):
                        raise HTTPException(503,'Désactivez le mode hors ligne pour communiquer avec le site public.')
                    if kind=='pull': return cloud_sync.pull(cfg,conn)
                    return cloud_sync.push(cfg,conn)
        except cloud_sync.CloudSyncError as error:
            raise HTTPException(503,str(error)) from None
        finally: _lock.release()

    @app.post('/api/cloud/sync')
    def sync(request:Request): return operation(request,'pull')

    @app.post('/api/cloud/publish')
    def publish(request:Request): return operation(request,'push')
