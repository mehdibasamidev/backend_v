# apps/payments/services/revenue_split_service.py
from apps.payments.models.revenue_split import RevenueSplit
from apps.payments.models.payment_transaction import PaymentTransaction
from apps.payments.models.coach_service import CoachServiceRequest


class RevenueSplitService:

    @staticmethod
    def create_split(transaction: PaymentTransaction, beneficiary, amount: float, split_type='platform', percentage=None):
        """Create a revenue split entry"""
        return RevenueSplit.objects.create(
            transaction=transaction,
            beneficiary=beneficiary,
            amount=amount,
            split_type=split_type,
            percentage=percentage
        )

    @staticmethod
    def calculate_and_split(transaction: PaymentTransaction, coach_user=None, platform_user=None, coach_percentage=70):
        """
        Split transaction amount between coach and platform.
        coach_user: User instance of the coach
        platform_user: User instance of the platform (usually superuser or platform account)
        """
        if not coach_user or not platform_user:
            raise ValueError("Both coach_user and platform_user must be provided")

        coach_amount = round(transaction.amount * coach_percentage / 100, 2)
        platform_amount = round(transaction.amount - coach_amount, 2)

        splits = [
            RevenueSplitService.create_split(transaction, coach_user, coach_amount, 'coach', coach_percentage),
            RevenueSplitService.create_split(transaction, platform_user, platform_amount, 'platform', 100 - coach_percentage)
        ]
        return splits

    @staticmethod
    def create_for_coach_request(request: CoachServiceRequest, transaction: PaymentTransaction, coach_percentage=70):
        """
        Automatically split revenue for a coach service request
        """
        coach = request.service.coach
        # Platform user could be system account or superuser
        from django.contrib.auth import get_user_model
        platform_user = get_user_model().objects.filter(is_superuser=True).first()
        if not platform_user:
            raise ValueError("No platform account found for revenue split")

        return RevenueSplitService.calculate_and_split(
            transaction=transaction,
            coach_user=coach,
            platform_user=platform_user,
            coach_percentage=coach_percentage
        )
