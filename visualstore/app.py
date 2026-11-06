"""FastAPI assembly: opens the registry, starts the pool and scheduler, mounts routers."""

from __future__ import annotations

import hashlib
import hmac
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
from visualstore.api import jobs as jobs_api
from visualstore.api import overview as overview_api
from visualstore.api import profiles as profiles_api
from visualstore.api import settings as settings_api
from visualstore.api import system as system_api
from visualstore.auth import LoginRateLimiter, SessionSigner
from visualstore.config import Config
from visualstore.jobs.compute.pool import ComputePool
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.backup import plan_backups
from visualstore.jobs.scheduler import Scheduler
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
            planners=[partial(plan_backups, backup_dir=config.backup_dir)],
        )
        scheduler.requeue_interrupted()
        await scheduler.start()
        app.state.vs = AppState(
            config=config,
            registry=registry,
            pool=pool,
            scheduler=scheduler,
            signer=signer,
            limiter=LoginRateLimiter(),
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

    app.include_router(auth_api.router)
    app.include_router(system_api.router)
    app.include_router(profiles_api.router)
    app.include_router(jobs_api.router)
    app.include_router(settings_api.router)
    app.include_router(overview_api.router)
    app.include_router(backups_api.router)
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
