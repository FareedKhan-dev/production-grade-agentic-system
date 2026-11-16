"""Prometheus metrics configuration for the application.

This module sets up and configures Prometheus metrics for monitoring the application.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

# Request metrics. "endpoint" is the route template (/api/v1/auth/session/{session_id}/name),
# not the raw path: one label value per session ID would grow without bound.
http_requests_total = Counter("http_requests_total", "Total number of HTTP requests", ["method", "endpoint", "status"])

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["method", "endpoint"]
)

# Database metrics: connections checked out of the pool, read when Prometheus scrapes
db_connections = Gauge("db_connections", "Number of active database connections")

# LLM calls take seconds, not milliseconds, so the buckets go up to a minute
llm_inference_duration_seconds = Histogram(
    "llm_inference_duration_seconds",
    "Time spent processing LLM inference",
    ["model"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

llm_stream_duration_seconds = Histogram(
    "llm_stream_duration_seconds",
    "Time spent processing LLM stream inference",
    ["model"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 60.0],
)


async def metrics(request: Request) -> Response:
    """Serve every metric in the Prometheus text format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def setup_metrics(app):
    """Set up the Prometheus metrics endpoint.

    The request metrics are recorded by MetricsMiddleware (src/system/middleware.py).

    Args:
        app: FastAPI application instance
    """
    # Add metrics endpoint
    app.add_route("/metrics", metrics)
