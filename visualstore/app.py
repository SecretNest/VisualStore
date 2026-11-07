"""FastAPI assembly: opens the registry, starts the pool and scheduler, mounts routers."""

from __future__ import annotations

import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from visualstore import __version__
from visualstore.api import auth as auth_api
from visualstore.api import backups as backups_api
from visualstore.api import bulk as bulk_api
from visualstore.api import files as files_api
from visualstore.api import folders as folders_api
from visualstore.api import groups as groups_api
from visualstore.api import jobs as jobs_api
from visualstore.api import models as models_api
from visualstore.api import operations as operations_api
from visualstore.api import overview as overview_api
from visualstore.api import profiles as profiles_api
from visualstore.api import settings as settings_api
from visualstore.api import system as system_api
from visualstore.api import trash as trash_api
from visualstore.auth import LoginRateLimiter, SessionSigner
from visualstore.config import Config
from visualstore.db.folders import FolderError
from visualstore.jobs.compute.pool import ComputePool
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.backup import plan_backups
from visualstore.jobs.io.planners import plan_maintenance, plan_purge, plan_regroup, plan_scans
from visualstore.jobs.scheduler import Scheduler
from visualstore.jobs.startup import check_consistency, check_indexes
from visualstore.profiles import ProfileError, ProfileNotFound, ProfileRegistry

DEFAULT_STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class AppState:
    config: Config
    registry: ProfileRegistry
    pool: ComputePool
    scheduler: Scheduler
    signer: SessionSigner
    limiter: LoginRateLimiter
    started_at: float = 0.0  # monotonic clock at start-up, for the health probe
    sessions_valid_from: float = 0.0  # tokens issued before this (epoch seconds) are rejected


def create_app(config: Config, static_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry = ProfileRegistry(config.data_dir)
        registry.open()
        # The signing key mixes the stored random secret with the configured password, so
        # changing VISUALSTORE_PASSWORD (env edit + restart) invalidates every session
        # without a session table (design §9.2).
        signer = SessionSigner(
            hmac.new(
                registry.secret("session_key"), config.password.encode("utf-8"), hashlib.sha256
            ).digest()
        )
        pool = ComputePool(config.compute_processes)
        pool.start()
        scheduler = Scheduler(
            registry,
            pool,
            io_handlers=IO_HANDLERS,
            backup_dir=config.backup_dir,
            model_dir=config.model_dir,
            planners=[
                partial(plan_backups, backup_dir=config.backup_dir),
                plan_scans,
                plan_purge,
                plan_regroup,
                plan_maintenance,
            ],
        )
        scheduler.requeue_interrupted()
        check_consistency(registry, config.model_dir)
        check_indexes(registry, config.model_dir)
        await scheduler.start()
        app.state.vs = AppState(
            config=config,
            registry=registry,
            pool=pool,
            scheduler=scheduler,
            signer=signer,
            limiter=LoginRateLimiter(),
            started_at=time.monotonic(),
            sessions_valid_from=registry.get_stamp("sessions_valid_from"),
        )
        try:
            yield
        finally:
            await scheduler.stop()
            pool.stop()
            registry.close()

    app = FastAPI(title="VisualStore", version=__version__, lifespan=lifespan)

    @app.exception_handler(ProfileNotFound)
    async def _profile_not_found(request: Request, exc: ProfileNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ProfileError)
    async def _profile_error(request: Request, exc: ProfileError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(FolderError)
    async def _folder_error(request: Request, exc: FolderError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(auth_api.router)
    app.include_router(system_api.router)
    app.include_router(system_api.health_router)
    app.include_router(profiles_api.router)
    app.include_router(jobs_api.router)
    app.include_router(settings_api.router)
    app.include_router(overview_api.router)
    app.include_router(backups_api.router)
    app.include_router(folders_api.router)
    app.include_router(bulk_api.router)  # before groups: /groups/bulk/* must not match {group_id}
    app.include_router(groups_api.router)
    app.include_router(files_api.router)
    app.include_router(models_api.router)
    app.include_router(trash_api.router)
    app.include_router(operations_api.router)
    _mount_static(app, static_dir or DEFAULT_STATIC_DIR)
    return app


def _mount_static(app: FastAPI, static_dir: Path) -> None:
    """Serve the frontend build. Registered after every API router so /api keeps its 404s."""
    index = static_dir / "index.html"
    if not index.is_file():
        return
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/") or path == "api":
            raise HTTPException(status_code=404, detail="Not found.")
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)
