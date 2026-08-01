from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.vpn.models import UserVpnSubscription, SubscriptionStatusChoices
from apps.vpn.services.provisioning import sync_subscription_usage


class Command(BaseCommand):
    help = (
        "Pulls current traffic usage from the 3x-ui panel for active "
        "subscriptions and marks them expired when they run out. "
        "Intended to run on a schedule (cron)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=15,
            help=(
                "Only sync subscriptions not synced within this many minutes. "
                "Keeps repeated runs cheap; use 0 to force every one."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max subscriptions per run (0 = no limit).",
        )
        parser.add_argument(
            "--include-expired",
            action="store_true",
            help=(
                "Also sync EXPIRED subscriptions. Syncing can now revive one "
                "(an admin may have topped the client up directly on the "
                "panel), so run this on a slower schedule than the default."
            ),
        )

    def handle(self, *args, **options):
        stale_minutes = options["stale_minutes"]
        limit = options["limit"]

        statuses = [SubscriptionStatusChoices.ACTIVE]
        if options["include_expired"]:
            statuses.append(SubscriptionStatusChoices.EXPIRED)

        # Hidden ones were cleared by the customer - no point spending a
        # panel round-trip keeping their numbers fresh.
        queryset = UserVpnSubscription.objects.filter(
            status__in=statuses,
            hidden_at__isnull=True,
        ).exclude(xui_client_email__isnull=True).exclude(xui_client_email="")

        if stale_minutes > 0:
            cutoff = timezone.now() - timedelta(minutes=stale_minutes)
            # Never-synced rows have last_synced_at NULL, so include those too.
            queryset = queryset.filter(
                Q(last_synced_at__lt=cutoff) | Q(last_synced_at__isnull=True)
            )

        # Oldest first, so a --limit run works through the backlog fairly
        # instead of repeatedly refreshing the same few rows.
        queryset = queryset.order_by("last_synced_at")

        if limit > 0:
            queryset = queryset[:limit]

        synced = 0
        expired = 0
        revived = 0
        failed = 0

        for subscription in queryset:
            try:
                before = subscription.status
                sync_subscription_usage(subscription)
                synced += 1
                if (
                    before == SubscriptionStatusChoices.ACTIVE
                    and subscription.status == SubscriptionStatusChoices.EXPIRED
                ):
                    expired += 1
                elif (
                    before == SubscriptionStatusChoices.EXPIRED
                    and subscription.status == SubscriptionStatusChoices.ACTIVE
                ):
                    revived += 1
            except Exception as e:
                # One unreachable client must not abort the whole run - the
                # panel may be briefly down, or a client deleted by hand.
                failed += 1
                self.stderr.write(
                    f"sync failed for {subscription.xui_client_email}: {e}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"synced={synced} newly_expired={expired} "
                f"revived={revived} failed={failed}"
            )
        )
