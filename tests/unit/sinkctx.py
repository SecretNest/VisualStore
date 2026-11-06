from pathlib import Path

from visualstore.db import settings as settings_db
from visualstore.jobs.results import SinkContext


def make_sink_ctx(
    registry, profile: str, params: dict, result: dict, model_dir: Path | None = None
) -> SinkContext:
    conn = registry.conn(profile)
    return SinkContext(
        conn=conn,
        vconn=registry.vectors_conn(profile),
        registry=registry,
        profile=profile,
        paths=registry.paths(profile),
        settings=settings_db.all(conn),
        model_dir=model_dir or registry.data_dir / "models",
        params=params,
        result=result,
    )
