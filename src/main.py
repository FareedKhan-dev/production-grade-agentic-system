"""This file contains the main application entry point."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from typing import (
    Any,
    Dict,
)

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langfuse import Langfuse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.config.settings import (
    JWT_SECRET_HINT,
    check_jwt_settings,
    settings,
)
from src.system.logs import logger

# Fail closed: with a missing, short or published JWT secret anyone could sign a token for
# any user, so the API refuses to start instead of running with it. This runs before the
# imports below, which connect to the database.
jwt_problem = check_jwt_settings(settings.JWT_SECRET_KEY, settings.JWT_ALGORITHM)
if jwt_problem:
    logger.critical("refusing_to_start", reason=jwt_problem)
    raise RuntimeError(f"Refusing to start: {jwt_problem}. {JWT_SECRET_HINT}")

from src.data.db_manager import db_manager  # noqa: E402
from src.interface.router import api_router  # noqa: E402
from src.system.middleware import (  # noqa: E402
    LoggingContextMiddleware,
    MetricsMiddleware,
)
from src.system.rate_limit import limiter  # noqa: E402
from src.system.telemetry import setup_metrics  # noqa: E402

# Load environment variables
load_dotenv()

# Initialize Langfuse (tracing is off when the keys are not set)
langfuse = Langfuse(
    public_key=settings.LANGFUSE_PUBLIC_KEY or None,
    secret_key=settings.LANGFUSE_SECRET_KEY or None,
    host=settings.LANGFUSE_HOST,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    # asyncio.to_thread runs on the loop's default executor, which holds CPU count + 4 threads.
    # Here the blocking work is I/O (database queries, mem0's model and embedding calls), so
    # give it enough threads not to queue behind a few slow model calls.
    executor = ThreadPoolExecutor(max_workers=settings.THREAD_POOL_SIZE, thread_name_prefix="io")
    asyncio.get_running_loop().set_default_executor(executor)
    logger.info(
        "application_startup",
        project_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        api_prefix=settings.API_V1_STR,
        thread_pool_size=settings.THREAD_POOL_SIZE,
    )
    yield
    logger.info("application_shutdown")
    # Send the traces still buffered, or they are lost when the container stops
    langfuse.flush()
    executor.shutdown(wait=False)


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Set up Prometheus metrics
setup_metrics(app)

# Middleware. Starlette wraps each one added around those added before it, so the last one
# added sees the request first: CORS, then the metrics, then the logging context, then the
# route. The logging context sits closest to the route, which is all it needs: it binds the
# IDs for the logs written while the route runs.
app.add_middleware(LoggingContextMiddleware)

# Add custom metrics middleware
app.add_middleware(MetricsMiddleware)

# Set up rate limiter exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Add validation exception handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors from request data.

    Args:
        request: The request that caused the validation error
        exc: The validation error

    Returns:
        JSONResponse: A formatted error response
    """
    # Log the validation error (a warning: it is usually the client's fault, not ours)
    logger.warning(
        "validation_error",
        client_host=request.client.host if request.client else "unknown",
        path=request.url.path,
        errors=str(exc.errors()),
    )

    # Format the errors to be more user-friendly
    formatted_errors = []
    for error in exc.errors():
        loc = " -> ".join([str(loc_part) for loc_part in error["loc"] if loc_part != "body"])
        formatted_errors.append({"field": loc, "message": error["msg"]})

    return JSONResponse(
        status_code=422,
        content={"detail": "Validation error", "errors": formatted_errors},
    )


# Set up CORS middleware. With "*" and credentials both allowed, Starlette answers any
# website's cookie-carrying request with that website's origin, which is unsafe as soon as
# anything uses cookies. The API authenticates with bearer tokens, so it does not need
# credentialed requests unless the allowed origins are listed explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials="*" not in settings.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["root"][0])
async def root(request: Request):
    """Root endpoint returning basic API information."""
    logger.info("root_endpoint_called")
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "healthy",
        "environment": settings.ENVIRONMENT.value,
        "swagger_url": "/docs",
        "redoc_url": "/redoc",
    }


@app.get("/health")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
async def health_check(request: Request) -> Dict[str, Any]:
    """Health check endpoint with environment-specific information.

    Returns:
        Dict[str, Any]: Health status information
    """
    logger.info("health_check_called")

    # Check database connectivity
    db_healthy = await db_manager.health_check()

    response = {
        "status": "healthy" if db_healthy else "degraded",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT.value,
        "components": {"api": "healthy", "database": "healthy" if db_healthy else "unhealthy"},
        "timestamp": datetime.now().isoformat(),
    }

    # If DB is unhealthy, set the appropriate status code
    status_code = status.HTTP_200_OK if db_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(content=response, status_code=status_code)
