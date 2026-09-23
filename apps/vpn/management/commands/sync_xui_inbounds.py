from django.core.management.base import BaseCommand, CommandError

from apps.vpn.services.inbounds import sync_inbounds_from_panel
from config.utils.exceptions import AppException


class Command(BaseCommand):
    help = (
        "Reads the inbound list from the 3x-ui panel into the local mirror "
        "that inbound groups are built from. Same as the Sync button in the "
        "admin panel's Inbounds tab."
    )

    def handle(self, *args, **options):
        try:
            inbounds = sync_inbounds_from_panel()
        except AppException as exc:
            raise CommandError(exc.message)

        for inbound in inbounds:
            state = "ok" if inbound.exists_on_panel else "MISSING on panel"
            if inbound.exists_on_panel and not inbound.is_enabled_on_panel:
                state = "disabled on panel"
            self.stdout.write(
                f"#{inbound.panel_id:<5} {inbound.protocol or '-':<12} "
                f"{inbound.port or '-':<6} {inbound.remark or '(no remark)'}  [{state}]"
            )

        missing = sum(1 for inbound in inbounds if not inbound.exists_on_panel)
        self.stdout.write(
            self.style.SUCCESS(f"synced={len(inbounds) - missing} missing={missing}")
        )
