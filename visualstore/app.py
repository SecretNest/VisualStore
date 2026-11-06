"""FastAPI assembly: opens the registry, starts the pool and scheduler, mounts routers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from visualstore import __version__
from visualstore.api import auth as auth_api
from visualstore.api import profiles as profiles_api
from visualstore.api import system as system_api
from visualstore.auth import LoginRateLimiter, SessionSigner
from visualstore.config import Config
from visualstore.jobs.compute.pool import ComputePool
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.backup import plan_backups
from visualstore.jobs.scheduler import Scheduler
from visualstore.profiles import ProfileError, ProfileNotFound, ProfileRegistry


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
        signer = SessionSigner(registry.secret("session_key"))
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
    # Task 11 and Task 12 add their routers here. Task 14 adds static files after the routers.
    return app
