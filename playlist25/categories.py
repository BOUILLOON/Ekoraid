"""Administrator category catalogue and proposal-only assignments."""
from __future__ import annotations

from contextlib import closing
import json
import re
import unicodedata
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from . import db
from .security import require_admin, require_csrf

ID_PATTERN = r"^[0-9a-f]{32}$"


class CategoryIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_ids: list[StrictStr]


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=80)
    sort_order: StrictInt = Field(default=0, ge=0, le=2147483647)
    active: StrictBool = True


class CategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    sort_order: StrictInt | None = Field(default=None, ge=0, le=2147483647)
    active: StrictBool | None = None


def clean_name(value: str) -> str:
    # Match PostgreSQL btrim/name validation without rewriting Unicode forms.
    # Format characters, including emoji joiners, are legitimate label text.
    value = value.strip(" ")
    if not value or len(value) > 80 or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise ValueError("Choisissez un nom de catégorie de 1 à 80 caractères, sans caractère de contrôle.")
    return value


def ids(value) -> list[str]:
    if (not isinstance(value, list) or
            any(not isinstance(item, str) or not re.fullmatch(ID_PATTERN, item) for item in value) or
            len(set(value)) != len(value)):
        raise ValueError("Choisissez des catégories valides, sans doublon.")
    return list(value)


def catalogue(conn) -> list[dict]:
    return [dict(id=row["id"], name=row["name"], sort_order=row["sort_order"], active=bool(row["active"]))
            for row in conn.execute("SELECT id,name,sort_order,active FROM categories ORDER BY sort_order,name COLLATE NOCASE,id")]


def assign(conn, proposal_id: int, selected: list[str], *, owner_id: str | None = None) -> dict:
    row = conn.execute("SELECT guest_id,state,withdrawn_at,category_ids FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if (not row or row["withdrawn_at"] or
            owner_id is not None and (row["guest_id"] != owner_id or row["state"] not in {"pending", "approved"})):
        raise HTTPException(404, "Cette proposition n'est pas disponible.")
    try:
        selected = ids(selected)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    all_categories = catalogue(conn)
    available = {item["id"] for item in all_categories if item["active"]}
    # Archived tags stay visible and may be retained on the same proposal, but
    # cannot be newly assigned to another proposal.
    available.update(set(json.loads(row["category_ids"])) & {item["id"] for item in all_categories})
    if any(item not in available for item in selected):
        raise HTTPException(422, "Une catégorie a été archivée ou n'existe plus. Actualisez votre choix.")
    conn.execute("UPDATE proposals SET category_ids=?,categories_dirty=1 WHERE id=?",
                 (json.dumps(selected, separators=(",", ":")), proposal_id))
    item = next(item for item in db.list_proposals(conn, guest_id=owner_id, include_hidden=True) if item["id"] == proposal_id)
    item["mine"] = bool(owner_id and item["guest_id"] == owner_id)
    return item


def register_admin_categories(app, cfg):
    def protected(request: Request):
        require_admin(request, cfg)

    def modifying(request: Request):
        require_admin(request, cfg)
        require_csrf(request)

    @app.get("/api/categories", dependencies=[Depends(protected)])
    def categories_list():
        with closing(db.connect(cfg)) as conn:
            return {"categories": catalogue(conn)}

    @app.post("/api/categories", dependencies=[Depends(modifying)])
    def category_create(payload: CategoryCreate):
        try:
            name = clean_name(payload.name)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        with closing(db.connect(cfg)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if payload.active and any(item["active"] and item["name"].lower() == name.lower() for item in catalogue(conn)):
                raise HTTPException(409, "Une catégorie porte déjà ce nom. Vous pouvez la renommer ou la réactiver.")
            category_id = uuid4().hex
            conn.execute("INSERT INTO categories(id,name,sort_order,active,dirty) VALUES(?,?,?,?,1)",
                         (category_id, name, payload.sort_order, int(payload.active)))
            items = catalogue(conn)
            return {"category": next(item for item in items if item["id"] == category_id), "categories": items}

    @app.patch("/api/categories/{category_id}", dependencies=[Depends(modifying)])
    def category_update(category_id: str, payload: CategoryPatch):
        values = payload.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise HTTPException(422, "Indiquez un nom, un ordre ou l'état de la catégorie.")
        if "name" in values:
            try:
                values["name"] = clean_name(values["name"])
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
        with closing(db.connect(cfg)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT name,active FROM categories WHERE id=?", (category_id,)).fetchone()
            if not current:
                raise HTTPException(404, "Cette catégorie n'existe pas.")
            final_name = values.get("name", current["name"])
            final_active = values.get("active", bool(current["active"]))
            new_name = final_name.lower() != current["name"].lower()
            activating = final_active and not current["active"]
            if final_active and (new_name or activating) and any(item["active"] and item["id"] != category_id and item["name"].lower() == final_name.lower() for item in catalogue(conn)):
                raise HTTPException(409, "Une catégorie porte déjà ce nom.")
            for key, value in values.items():
                conn.execute(f"UPDATE categories SET {key}=?,dirty=1 WHERE id=?", (int(value) if key == "active" else value, category_id))
            items = catalogue(conn)
            return {"category": next(item for item in items if item["id"] == category_id), "categories": items}

    @app.patch("/api/proposals/{proposal_id}/categories", dependencies=[Depends(modifying)])
    def proposal_categories(proposal_id: int, payload: CategoryIds):
        with closing(db.connect(cfg)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return {"item": assign(conn, proposal_id, payload.category_ids)}

    @app.post("/api/categories/publish", dependencies=[Depends(modifying)])
    def publish_categories():
        from .cloud_sync import publish_categories as publish
        from .private_config import cloud_settings_snapshot
        with closing(db.connect(cfg)) as conn, conn:
            try:
                return publish(cloud_settings_snapshot(cfg), conn)
            except ValueError as error:
                raise HTTPException(409, str(error)) from None
