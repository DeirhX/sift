"""The web stage: SQLite ingest, schema/domain authority, read layer, the
reanalysis subsystem and the FastAPI review server.

Layering (no cycles):

    server.py  ── routing, runtime config, mutations, file I/O, app wiring
       │            └──► analysis.py  (reanalysis jobs + payload->argv)
       ├──► queries.py  (pure conn-parameterized SQL -> DTO helpers)
       └──► photodb.py  (schema + domain rules)  ◄── also used by build_db.py
"""
