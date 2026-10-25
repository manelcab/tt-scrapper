"""Capa de almacenamiento: selección de backend.

`make_storage(cfg)` devuelve el cliente según `cfg.storage_backend`:
- 'firestore' (por defecto): escribe en Firestore.
- 'd1': emite un .sql idempotente para cargar en Cloudflare D1 con wrangler.

Los dos exponen la misma interfaz, así que el CLI y los scrapers no cambian.
Los imports son perezosos para no exigir las dependencias del backend que no se usa.
"""

from __future__ import annotations


def make_storage(cfg):
    backend = (getattr(cfg, "storage_backend", "firestore") or "firestore").lower()
    if backend == "d1":
        from storage.d1_client import D1Client

        return D1Client(cfg.sql_out_dir)
    from storage.firestore_client import FirestoreClient

    return FirestoreClient(cfg.firestore_project_id, cfg.google_credentials)
