# config/utils/exceptions.py

class AppException(Exception):
    """
    Base application exception.
    Used for service-layer errors.
    """
    default_message = "Application error"

    def __init__(self, message=None):
        self.message = message or self.default_message
        super().__init__(self.message)


class BadRequestException(AppException):
    default_message = "Bad request"


class NotFoundException(AppException):
    default_message = "Resource not found"


class ForbiddenException(AppException):
    default_message = "You do not have permission to perform this action"
