from rest_framework.permissions import BasePermission


class CanAssignWorkoutProgram(BasePermission):
    """
    Only coaches or gym owners can assign programs
    """

    def has_permission(self, request, view):
        return bool(
            request.user.is_authenticated and
            request.user.is_active and
            (request.user.is_coach or request.user.is_gym_owner)
        )
