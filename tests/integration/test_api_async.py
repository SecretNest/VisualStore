import inspect

from fastapi.routing import APIRoute

from visualstore.app import create_app


def api_routes(app):
    """APIRoute objects, looking through FastAPI's included-router wrappers."""
    for route in app.routes:
        inner = getattr(route, "original_router", None)
        candidates = inner.routes if inner is not None else [route]
        for candidate in candidates:
            if isinstance(candidate, APIRoute) and candidate.path.startswith("/api"):
                yield candidate


def test_every_api_endpoint_runs_on_the_event_loop(config):
    """Registry connections are shared with the scheduler, which runs on the event loop.
    A sync endpoint runs in a threadpool and would use those connections concurrently;
    the final review reproduced aborted transactions and lost writes from exactly that."""
    routes = list(api_routes(create_app(config)))
    assert len(routes) >= 15  # guard against the walk silently finding nothing
    sync_endpoints = sorted(r.path for r in routes if not inspect.iscoroutinefunction(r.endpoint))
    assert sync_endpoints == []


def dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from dependency_calls(dep)


def test_every_api_dependency_runs_on_the_event_loop(config):
    """FastAPI runs sync dependencies in the threadpool too; get_profile queries global.db."""
    sync_deps = sorted(
        {
            f"{call.__module__}.{call.__name__}"
            for route in api_routes(create_app(config))
            for call in dependency_calls(route.dependant)
            if not inspect.iscoroutinefunction(call)
        }
    )
    assert sync_deps == []
