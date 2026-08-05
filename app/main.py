from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name}

    @app.get("/")
    def index() -> dict:
        spec = app.openapi()
        endpoints = sorted(
            {
                f"{method.upper()} {path}"
                for path, ops in spec["paths"].items()
                for method in ops
                if path.startswith("/api")
            }
        )
        return {
            "app": settings.app_name,
            "docs": "/docs",
            "redoc": "/redoc",
            "health": "/health",
            "endpoints": endpoints,
        }

    return app


app = create_app()