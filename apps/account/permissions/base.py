from rest_framework.permissions import BasePermission


class IsActiveUserPermission(BasePermission):
    """
    User must be authenticated and active
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.is_active
        )
