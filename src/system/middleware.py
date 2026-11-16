"""Custom middleware for tracking metrics and other cross-cutting concerns."""

import time
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.system.logs import (
    bind_context,
    clear_context,
)
from src.system.telemetry import (
    http_request_duration_seconds,
    http_requests_total,
)
from src.utils.auth import decode_token

# Scraped or polled every few seconds; counting them would drown the real traffic
UNTRACKED_PATHS = {"/metrics", "/health"}


def route_template(request: Request) -> str:
    """Return the path of the route that handled the request, with its parameters as placeholders.

    /api/v1/auth/session/3f2a.../name becomes /api/v1/auth/session/{session_id}/name, so one
    route is one time series however many sessions exist. Requests that matched no route
    (scanners probing random URLs) all share the label "unmatched".
    """
    if request.scope.get("endpoint") is None:
        return "unmatched"
    placeholders = {str(value): "{" + name + "}" for name, value in request.path_params.items()}
    return "/".join(placeholders.get(segment, segment) for segment in request.url.path.split("/"))


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking HTTP request metrics."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Track metrics for each request.

        Args:
            request: The incoming request
            call_next: The next middleware or route handler

        Returns:
            Response: The response from the application
        """
        start_time = time.time()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.time() - start_time

            if request.url.path not in UNTRACKED_PATHS:
                endpoint = route_template(request)
                http_requests_total.labels(method=request.method, endpoint=endpoint, status=status_code).inc()
                http_request_duration_seconds.labels(method=request.method, endpoint=endpoint).observe(duration)


class LoggingContextMiddleware(BaseHTTPMiddleware):
    """Middleware for adding user_id and session_id to logging context."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Extract user_id or session_id from authenticated requests and add it to the logging context.

        Args:
            request: The incoming request
            call_next: The next middleware or route handler

        Returns:
            Response: The response from the application
        """
        try:
            # Clear any existing context from previous requests
            clear_context()

            # Extract token from Authorization header
            auth_header = request.headers.get("authorization")
            if auth_header and auth_header.startswith("Bearer "):
                # Only a token with a valid signature is trusted: an unverified one would let
                # anyone write another user's ID into the logs. Invalid tokens are left for the
                # auth dependency to reject.
                claims = decode_token(auth_header.split(" ", 1)[1])
                if claims and claims.get("type") == "session":
                    bind_context(session_id=claims["sub"])
                elif claims and claims.get("type") == "user":
                    bind_context(user_id=claims["sub"])

            # Process the request
            return await call_next(request)

        finally:
            # Always clear context after request is complete to avoid leaking to other requests
            clear_context()
