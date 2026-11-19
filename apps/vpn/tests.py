from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.vpn.serializers.admin import AdminVpnPlanSerializer
from apps.vpn.services.provisioning import activate_subscription


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
