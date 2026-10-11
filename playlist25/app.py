"""Independent HTTP surfaces. The guest application never mounts the admin API."""
from .config import load_settings
from .db import init_db

def create_app(surface,cfg=None):
    cfg=cfg or load_settings()
    init_db(cfg)
    if surface=="guest":
        from .guest import create_guest_app
        app=create_guest_app(cfg)
        from .transfers import attach_guest_routes
        attach_guest_routes(app,cfg)
        return app
    if surface=="admin":
        from .admin import create_admin_app
        app=create_admin_app(cfg)
        from .cloud_routes import add_cloud_routes
        add_cloud_routes(app,cfg)
        from .transfers import attach_admin_routes
        attach_admin_routes(app,cfg)
        return app
    raise ValueError("Surface inconnue")
