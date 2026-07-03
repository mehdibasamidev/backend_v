from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework import status


class BaseAPIResponse(Response):
    """
    The Master Class. Every response in the app will follow this
    structure.
    """
    def __init__(self, data=None, message="success", code="SUCCESS", errors=None, status_code=status.HTTP_200_OK, **kwargs):
        response_data = {
            "status": status.is_success(status_code),  # Automatically sets True for 2xx, False otherwise
            "code": code,
            "message": message,
            "data": data if data is not None else {},
            "errors": errors if errors is not None else {}
        }
        super().__init__(response_data, status=status_code, **kwargs)


# --- SUCCESS RESPONSES (2xx) ---

class SuccessResponse(BaseAPIResponse):
    def __init__(self, data=None, message="Success", code="SUCCESS", **kwargs):
        super().__init__(data=data, message=message, code=code, status_code=status.HTTP_200_OK, **kwargs)


class SuccessResponse201(BaseAPIResponse):
    def __init__(self, data=None, message="Created Successfully", code="CREATED", **kwargs):
        super().__init__(data=data, message=message, code=code, status_code=status.HTTP_201_CREATED, **kwargs)


# --- CLIENT ERROR RESPONSES (4xx) ---

class BadRequestResponse(BaseAPIResponse):
    def __init__(self, errors=None, message=None, code="BAD_REQUEST", data=None, **kwargs):
        # 1. Handle raw Django/DRF ValidationErrors passed as 'errors'
        if isinstance(errors, (DjangoValidationError, DRFValidationError)):
            if hasattr(errors, 'message_dict'):
                # It's a dict-style error
                extracted_errors = errors.message_dict
                message = message or extracted_errors[next(iter(extracted_errors))][0]
                errors = extracted_errors
            elif hasattr(errors, 'messages'):
                # It's a list-style error (like your OTP rate limit)
                message = message or errors.messages[0]
                errors = {"detail": errors.messages}

        # 2. Existing logic for dict-based errors
        elif message is None and isinstance(errors, dict):
            try:
                first_field = next(iter(errors))
                error_item = errors[first_field]
                message = error_item[0] if isinstance(error_item, list) else str(error_item)
            except Exception:
                message = "Validation Failed"

        super().__init__(
            data=data,
            errors=errors,
            message=message or "Validation Failed",
            code=code,
            status_code=status.HTTP_400_BAD_REQUEST,
            **kwargs
        )


class AuthErrorResponse(BaseAPIResponse):
    """Used for 401 Unauthorized (Expired tokens, etc)."""
    def __init__(self, message="Authentication required", code="UNAUTHORIZED", data=None, **kwargs):
        super().__init__(data=data, message=message, code=code, status_code=status.HTTP_401_UNAUTHORIZED, **kwargs)


class ForbiddenResponse(BaseAPIResponse):
    """Used for 403 Forbidden (User doesn't have permission)."""
    def __init__(self, message="You do not have permission to perform this action", code="FORBIDDEN", data=None, **kwargs):
        super().__init__(data=data, message=message, code=code, status_code=status.HTTP_403_FORBIDDEN, **kwargs)


class NotFoundResponse(BaseAPIResponse):
    """Used for 404 Not Found."""
    def __init__(self, message="Resource not found", code="NOT_FOUND", data=None, **kwargs):
        super().__init__(data=data, message=message, code=code, status_code=status.HTTP_404_NOT_FOUND, **kwargs)


# --- SERVER ERROR RESPONSES (5xx) ---

class ServerErrorResponse(BaseAPIResponse):
    """Used for 500 Internal Server Errors."""
    def __init__(self, message="Something went wrong on our end", code="SERVER_ERROR", errors=None, data=None, **kwargs):
        super().__init__(data=data, errors=errors, message=message, code=code, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, **kwargs)
