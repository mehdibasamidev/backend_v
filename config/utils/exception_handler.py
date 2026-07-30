import logging
import traceback
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework import status

from config.utils.exceptions import (
    AppException,
    BadRequestException,
    NotFoundException,
    ForbiddenException,
)
from config.utils.response import (
    AuthErrorResponse,
    BadRequestResponse,
    ForbiddenResponse,
    NotFoundResponse,
    ServerErrorResponse,
)

logger = logging.getLogger("apps")


def custom_exception_handler(exc, context):
    """
    Wired up via REST_FRAMEWORK['EXCEPTION_HANDLER'].

    Every error - ours, DRF's, or an outright bug - leaves through here in
    the same BaseAPIResponse envelope, so the Flutter client only ever has
    one error shape to parse. (DRF's own handler would answer 401/403/404
    and validation errors as {"detail": ...}, which does not match.)

    It also guarantees unexpected exceptions get logged with a full
    traceback instead of silently becoming Django's bare HTML 500.
    """
    view = context.get("view").__class__.__name__ if context.get("view") else "?"
    request = context.get("request")
    path = getattr(request, "path", "?")

    # --- Our own service-layer exceptions -----------------------------
    if isinstance(exc, BadRequestException):
        return BadRequestResponse(message=exc.message)
    if isinstance(exc, NotFoundException):
        return NotFoundResponse(message=exc.message)
    if isinstance(exc, ForbiddenException):
        return ForbiddenResponse(message=exc.message)
    if isinstance(exc, AppException):
        return BadRequestResponse(message=exc.message)

    # --- Django-level equivalents --------------------------------------
    if isinstance(exc, Http404):
        return NotFoundResponse()
    if isinstance(exc, DjangoValidationError):
        # BadRequestResponse already knows how to unwrap these.
        return BadRequestResponse(errors=exc)

    # --- DRF exceptions, remapped into our envelope --------------------
    if isinstance(exc, drf_exceptions.ValidationError):
        return BadRequestResponse(errors=exc.detail)

    if isinstance(exc, (drf_exceptions.NotAuthenticated,
                        drf_exceptions.AuthenticationFailed)):
        return AuthErrorResponse(message=_detail_message(exc))

    if isinstance(exc, drf_exceptions.PermissionDenied):
        return ForbiddenResponse(message=_detail_message(exc))

    if isinstance(exc, drf_exceptions.NotFound):
        return NotFoundResponse(message=_detail_message(exc))

    if isinstance(exc, drf_exceptions.APIException):
        # Everything else DRF defines (throttled, method not allowed,
        # unsupported media type, ...). Keep its status code and code
        # string rather than flattening them all to 400.
        code = getattr(exc, "default_code", "error")
        if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            return ServerErrorResponse(message=_detail_message(exc), code=code.upper())
        return BadRequestResponse(
            message=_detail_message(exc),
            code=code.upper(),
            errors=exc.detail if isinstance(exc.detail, dict) else None,
        )

    # --- Anything else is a real bug -----------------------------------
    # A short id lets you match what a user reports to the traceback in
    # the logs without leaking internals in the response body.
    error_id = uuid.uuid4().hex[:8]
    logger.error(
        "Unhandled exception [%s] in %s (%s): %s\n%s",
        error_id,
        view,
        path,
        exc,
        traceback.format_exc(),
    )

    # With DEBUG on, surface the real message so local work stays fast.
    if settings.DEBUG:
        return ServerErrorResponse(
            message=f"{exc.__class__.__name__}: {exc}",
            errors={"error_id": error_id, "traceback": traceback.format_exc().splitlines()},
        )

    return ServerErrorResponse(
        message="An unexpected error occurred. Please contact support with the reference id.",
        errors={"error_id": error_id},
    )


def _detail_message(exc):
    """DRF details can be a string, a list, or a dict - flatten to one line."""
    detail = getattr(exc, "detail", None)
    if detail is None:
        return str(exc)
    if isinstance(detail, dict):
        first = next(iter(detail.values()), "")
        return str(first[0] if isinstance(first, list) and first else first)
    if isinstance(detail, list):
        return str(detail[0]) if detail else str(exc)
    return str(detail)
