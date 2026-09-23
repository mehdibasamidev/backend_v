from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAdminUser
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from apps.vpn.models import XuiInbound
from apps.vpn.serializers.admin import (
    AdminInboundGroupSerializer,
    AdminXuiInboundSerializer,
)
from apps.vpn.services.inbounds import (
    create_inbound_group,
    delete_inbound_group,
    inbound_groups_with_details,
    sync_inbounds_from_panel,
    update_inbound_group,
)
from config.utils.custom_serializers import create_response_serializer
from config.utils.response import (
    SuccessResponse,
    SuccessResponse201,
    BadRequestResponse,
    NotFoundResponse,
)


# ==========================================================
# Inbounds (mirror of the 3x-ui panel)
# ==========================================================

class AdminInboundListView(APIView):
    """
    The local mirror, not paginated: a panel has a handful of inbounds and
    the group editor needs all of them at once.
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        operation_description="Every mirrored inbound, ordered by panel id. data is a list.",
        responses={200: create_response_serializer(
            data_serializer_class=AdminXuiInboundSerializer,
            text_message="Inbounds retrieved successfully",
        )},
    )
    def get(self, request):
        inbounds = XuiInbound.objects.order_by("panel_id")
        return SuccessResponse(
            data=AdminXuiInboundSerializer(inbounds, many=True).data,
            message="Inbounds retrieved successfully",
        )


class AdminInboundSyncView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(
        operation_description=(
            "Reads the inbound list from the 3x-ui panel and updates the mirror. "
            "Inbounds the panel no longer has are kept and flagged exists_on_panel=false. "
            "data is a list."
        ),
        responses={200: create_response_serializer(
            data_serializer_class=AdminXuiInboundSerializer,
            text_message="Inbounds synced from the panel",
        )},
    )
    def post(self, request):
        # A panel failure raises InboundSyncError, which the exception
        # handler turns into a 400 with the reason.
        inbounds = sync_inbounds_from_panel()
        return SuccessResponse(
            data=AdminXuiInboundSerializer(inbounds, many=True).data,
            message="Inbounds synced from the panel",
        )


# ==========================================================
# Inbound groups
# ==========================================================

class AdminInboundGroupListCreateView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(
        operation_description="Every group, default first, then by name. Not paginated; data is a list.",
        responses={200: create_response_serializer(
            data_serializer_class=AdminInboundGroupSerializer,
            text_message="Inbound groups retrieved successfully",
        )},
    )
    def get(self, request):
        groups = inbound_groups_with_details()
        return SuccessResponse(
            data=AdminInboundGroupSerializer(groups, many=True).data,
            message="Inbound groups retrieved successfully",
        )

    @swagger_auto_schema(
        request_body=AdminInboundGroupSerializer,
        responses={201: create_response_serializer(
            data_serializer_class=AdminInboundGroupSerializer,
            text_message="Inbound group created successfully",
        )},
    )
    def post(self, request):
        serializer = AdminInboundGroupSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        data = serializer.validated_data
        group = create_inbound_group(
            name=data["name"],
            inbounds=data["inbounds"],
            is_default=data.get("is_default", False),
        )
        return SuccessResponse201(
            data=AdminInboundGroupSerializer(inbound_groups_with_details().get(pk=group.pk)).data,
            message="Inbound group created successfully",
        )


class AdminInboundGroupDetailView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def _get_group(self, group_id):
        return inbound_groups_with_details().filter(id=group_id).first()

    @swagger_auto_schema(
        responses={200: create_response_serializer(
            data_serializer_class=AdminInboundGroupSerializer,
            text_message="Inbound group retrieved successfully",
        )},
    )
    def get(self, request, group_id):
        group = self._get_group(group_id)
        if not group:
            return NotFoundResponse(message="Inbound group not found")
        return SuccessResponse(
            data=AdminInboundGroupSerializer(group).data,
            message="Inbound group retrieved successfully",
        )

    @swagger_auto_schema(
        request_body=AdminInboundGroupSerializer,
        responses={200: create_response_serializer(
            data_serializer_class=AdminInboundGroupSerializer,
            text_message="Inbound group updated successfully",
        )},
    )
    def patch(self, request, group_id):
        group = self._get_group(group_id)
        if not group:
            return NotFoundResponse(message="Inbound group not found")

        serializer = AdminInboundGroupSerializer(group, data=request.data, partial=True)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        data = serializer.validated_data
        update_inbound_group(
            group,
            name=data.get("name"),
            inbounds=data.get("inbounds"),
            is_default=data.get("is_default"),
        )
        return SuccessResponse(
            data=AdminInboundGroupSerializer(self._get_group(group.pk)).data,
            message="Inbound group updated successfully",
        )

    def delete(self, request, group_id):
        group = self._get_group(group_id)
        if not group:
            return NotFoundResponse(message="Inbound group not found")

        # Refused with a 400 while it is the default or plans use it.
        delete_inbound_group(group)
        return SuccessResponse(message="Inbound group deleted successfully")
