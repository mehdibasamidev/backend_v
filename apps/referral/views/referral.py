from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from apps.referral.models import (
    Referral,
    ReferralCode,
    ReferralCodeKind,
    ReferralSettings,
)
from apps.referral.serializers.referral import (
    AdminReferralCodeCreateSerializer,
    AdminReferralSerializer,
    PublicReferralSettingsSerializer,
    ReferralCodeSerializer,
    ReferralSerializer,
    ReferralSettingsSerializer,
)
from apps.referral.services.redemption import (
    get_or_create_personal_code,
    resolve_code,
)
from config.utils.exceptions import AppException
from config.utils.pagination import StandardResultsSetPagination
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    NotFoundResponse,
)


class ReferralSettingsPublicView(APIView):
    """Tells the signup screen whether to show and require the code field."""
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        return SuccessResponse(
            data=PublicReferralSettingsSerializer(
                ReferralSettings.get_solo()
            ).data,
            message="Referral settings retrieved successfully.",
        )


class ValidateReferralCodeView(APIView):
    """
    Checks a code before signup so the field can go green as it's typed,
    rather than failing after the whole form is submitted.

    Open by design: an invite code is meant to be shared, so confirming one
    exists reveals nothing that isn't already public to whoever holds it.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def post(self, request):
        try:
            code = resolve_code(request.data.get("code", ""))
        except AppException as e:
            return BadRequestResponse(message=e.message)

        return SuccessResponse(
            data={"code": code.code, "is_valid": True},
            message="This invite code is valid.",
        )


class MyReferralCodeView(APIView):
    """The caller's own invite code, plus who has used it."""
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        try:
            code = get_or_create_personal_code(request.user)
        except AppException as e:
            return BadRequestResponse(message=e.message)

        referrals = Referral.objects.filter(referrer=request.user)
        return SuccessResponse(
            data={
                "code": ReferralCodeSerializer(code).data,
                "total_referrals": referrals.count(),
                "referrals": ReferralSerializer(referrals[:20], many=True).data,
            },
            message="Invite code retrieved successfully.",
        )


# ------------------------------------------------------------------ admin

class AdminReferralSettingsView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def get(self, request):
        return SuccessResponse(
            data=ReferralSettingsSerializer(ReferralSettings.get_solo()).data,
            message="Referral settings retrieved successfully.",
        )

    @swagger_auto_schema(request_body=ReferralSettingsSerializer)
    def patch(self, request):
        settings_row = ReferralSettings.get_solo()
        serializer = ReferralSettingsSerializer(
            settings_row, data=request.data, partial=True
        )
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        serializer.save()
        return SuccessResponse(
            data=serializer.data, message="Referral settings updated."
        )


class AdminReferralCodeListCreateView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        codes = ReferralCode.objects.select_related("owner").all()

        kind = request.GET.get("kind")
        if kind in (ReferralCodeKind.PERSONAL, ReferralCodeKind.ADMIN):
            codes = codes.filter(kind=kind)

        query = request.GET.get("q", "").strip()
        if query:
            codes = codes.filter(code__icontains=query)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(codes, request, view=self)
        return paginator.get_paginated_response(
            ReferralCodeSerializer(page, many=True).data
        )

    @swagger_auto_schema(request_body=AdminReferralCodeCreateSerializer)
    def post(self, request):
        serializer = AdminReferralCodeCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        data = serializer.validated_data
        raw = (data.get("code") or "").strip().upper().replace(" ", "")
        code_value = raw or ReferralCode.generate_code()

        if ReferralCode.objects.filter(code=code_value).exists():
            return BadRequestResponse(message="That code already exists.")

        code = ReferralCode.objects.create(
            code=code_value,
            kind=ReferralCodeKind.ADMIN,
            max_uses=data.get("max_uses", 0),
            expires_at=data.get("expires_at"),
            note=data.get("note", ""),
        )
        return SuccessResponse(
            data=ReferralCodeSerializer(code).data,
            message="Invite code created.",
        )


class AdminReferralCodeDetailView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def patch(self, request, code_id):
        try:
            code = ReferralCode.objects.get(id=code_id)
        except ReferralCode.DoesNotExist:
            return NotFoundResponse(message="Invite code not found.")

        serializer = ReferralCodeSerializer(code, data=request.data, partial=True)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        serializer.save()
        return SuccessResponse(data=serializer.data, message="Invite code updated.")

    def delete(self, request, code_id):
        try:
            code = ReferralCode.objects.get(id=code_id)
        except ReferralCode.DoesNotExist:
            return NotFoundResponse(message="Invite code not found.")

        # Deactivated, never deleted: redemptions PROTECT the row, and the
        # history of who invited whom has to survive for the reward system.
        code.is_active = False
        code.save(update_fields=["is_active"])
        return SuccessResponse(message="Invite code disabled.")


class AdminReferralListView(APIView):
    """
    Every redemption, newest first - the answer to "who came in through a
    code, and whose".

    Filterable by referrer so an admin can audit one person's invites, and
    by reward state so the payout run has a queue to work from once the
    wallet exists.
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        referrals = Referral.objects.select_related(
            "code", "referrer", "referred_user"
        ).all()

        referrer_id = request.GET.get("referrer")
        if referrer_id:
            referrals = referrals.filter(referrer_id=referrer_id)

        code = request.GET.get("code")
        if code:
            referrals = referrals.filter(code__code__iexact=code.strip().upper())

        rewarded = request.GET.get("rewarded")
        if rewarded in ("true", "false"):
            referrals = referrals.filter(is_rewarded=(rewarded == "true"))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(referrals, request, view=self)
        return paginator.get_paginated_response(
            AdminReferralSerializer(page, many=True).data
        )


class AdminReferralStatsView(APIView):
    """
    Headline numbers for the referral dashboard.

    Computed in the database rather than by walking the queryset - this is
    the kind of endpoint that gets polled, and counting rows in Python
    would get slower with every signup.
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        from django.db.models import Count

        total = Referral.objects.count()
        pending_reward = Referral.objects.filter(is_rewarded=False).count()

        top_referrers = list(
            Referral.objects.filter(referrer__isnull=False)
            .values("referrer_id", "referrer__username", "referrer__email")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        return SuccessResponse(
            data={
                "total_referrals": total,
                "pending_reward": pending_reward,
                "active_codes": ReferralCode.objects.filter(is_active=True).count(),
                "top_referrers": [
                    {
                        "user_id": str(row["referrer_id"]),
                        "username": row["referrer__username"],
                        "email": row["referrer__email"],
                        "referrals": row["count"],
                    }
                    for row in top_referrers
                ],
            },
            message="Referral stats retrieved successfully.",
        )
