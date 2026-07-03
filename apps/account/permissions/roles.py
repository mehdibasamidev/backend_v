from rest_framework.permissions import BasePermission


class IsCoach(BasePermission):
    """
    Allows access only to coaches
    """
    def has_permission(self, request, view):
        return bool(
            request.user.is_authenticated and
            request.user.is_active and
            request.user.is_coach
        )


class IsGymOwner(BasePermission):
    """
    Allows access only to gym owners
    """
    def has_permission(self, request, view):
        return bool(
            request.user.is_authenticated and
            request.user.is_active and
            request.user.is_gym_owner
        )


class IsCoachOrGymOwner(BasePermission):
    """
    Coach OR Gym Owner
    """
    def has_permission(self, request, view):
        return bool(
            request.user.is_authenticated and
            request.user.is_active and
            (request.user.is_coach or request.user.is_gym_owner)
        )
