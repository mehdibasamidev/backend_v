from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.vpn.models import InboundGroup, XuiInbound
from apps.vpn.serializers.admin import AdminVpnPlanSerializer
from apps.vpn.services.inbounds import InboundSyncError, sync_inbounds_from_panel
from apps.vpn.services.provisioning import activate_subscription
from apps.vpn.views.admin_inbounds import (
    AdminInboundGroupListCreateView,
    AdminInboundListView,
    AdminInboundSyncView,
)


class InboundSyncTests(TestCase):
    def test_sync_hides_and_detaches_missing_inbounds_but_keeps_disabled_ones(self):
        disabled = XuiInbound.objects.create(panel_id=1001)
        deleted = XuiInbound.objects.create(panel_id=1002)
        previously_missing = XuiInbound.objects.create(panel_id=1003, exists_on_panel=False)
        group = InboundGroup.objects.create(name="Test group")
        group.inbounds.add(disabled, deleted, previously_missing)

        request = APIRequestFactory().post("/")
        force_authenticate(request, user=SimpleNamespace(is_staff=True))
        with patch("apps.vpn.services.inbounds.fetch_panel_inbounds", return_value=[
            {"id": 1001, "enable": False},
        ]):
            response = AdminInboundSyncView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["panel_id"] for item in response.data["data"]], [1001])
        self.assertFalse(response.data["data"][0]["is_enabled_on_panel"])
        self.assertEqual(list(group.inbounds.values_list("panel_id", flat=True)), [1001])
        deleted.refresh_from_db()
        self.assertFalse(deleted.exists_on_panel)

        request = APIRequestFactory().get("/")
        force_authenticate(request, user=SimpleNamespace(is_staff=True))
        response = AdminInboundListView.as_view()(request)
        self.assertEqual([item["panel_id"] for item in response.data["data"]], [1001])

        request = APIRequestFactory().get("/")
        force_authenticate(request, user=SimpleNamespace(is_staff=True))
        response = AdminInboundGroupListCreateView.as_view()(request)
        result = next(item for item in response.data["data"] if item["name"] == group.name)
        self.assertEqual(result["inbound_ids"], [1001])

    def test_incomplete_snapshot_does_not_detach_members(self):
        inbound = XuiInbound.objects.create(panel_id=1001)
        group = InboundGroup.objects.create(name="Test group")
        group.inbounds.add(inbound)
        for rows in ([], [{"id": 1002}, {"id": None}]):
            with self.subTest(rows=rows), patch(
                "apps.vpn.services.inbounds.fetch_panel_inbounds", return_value=rows,
            ):
                with self.assertRaises(InboundSyncError):
                    sync_inbounds_from_panel()
                self.assertTrue(group.inbounds.filter(pk=inbound.pk).exists())
                self.assertFalse(XuiInbound.objects.filter(panel_id=1002).exists())


class UnlimitedPlanDurationTests(SimpleTestCase):
    def test_admin_duration_accepts_zero_but_rejects_negative(self):
        field = AdminVpnPlanSerializer().fields["duration_days"]
        self.assertEqual(field.run_validation(0), 0)
        self.assertEqual(field.run_validation(30), 30)
        with self.assertRaises(ValidationError):
            field.run_validation(-1)

    def test_activation_sends_no_expiry_for_zero_and_keeps_finite_duration(self):
        for days in (0, 30):
            with self.subTest(days=days):
                subscription = SimpleNamespace(
                    duration_days=days, volume_gb=1, max_concurrent_users=1,
                    is_unlimited_volume=False, is_unlimited_users=False,
                    xui_client_email="test-client", save=Mock(),
                )
                with patch("apps.vpn.services.provisioning.ThreeXUiClient") as factory, patch(
                    "apps.vpn.services.provisioning.resolve_inbound_ids", return_value=[1],
                ):
                    factory.return_value.get_client.return_value = {"client": {"uuid": "id", "subId": "sub"}}
                    activate_subscription(subscription)
                    expiry = factory.return_value.add_client.call_args.kwargs["expiry_time_ms"]
                if days == 0:
                    self.assertEqual(expiry, 0)
                    self.assertIsNone(subscription.expires_at)
                else:
                    self.assertEqual((subscription.expires_at - subscription.started_at).days, 30)
                    self.assertGreater(expiry, 0)
