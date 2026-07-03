# apps/payments/services/coach_request_service.py
from django.db import transaction

from apps.payments.models.coach_service import CoachService, CoachServiceRequest
from apps.payments.models.payment_transaction import PaymentTransaction
from config.utils.exceptions import ForbiddenException, NotFoundException
from apps.payments.services.revenue_split_service import RevenueSplitService


class CoachRequestService:

    @staticmethod
    def list_services(coach=None):
        """List all active coach services"""
        qs = CoachService.objects.filter(is_active=True)
        if coach:
            qs = qs.filter(coach=coach)
        return qs

    @staticmethod
    @transaction.atomic
    def create_request(*, client, data):
        """
        Create a coach service request.
        NO Stripe logic here.
        """

        service_id = data.get("service_id")

        service = CoachService.objects.select_related("coach").filter(
            id=service_id,
            is_active=True
        ).first()

        if not service:
            raise NotFoundException("Service not found or inactive")

        if service.coach_id == client.id:
            raise ForbiddenException("You cannot request your own service")

        request = CoachServiceRequest.objects.create(
            client=client,
            service=service,
            coach=service.coach,
            price=service.price,
            title=service.title,
            status=CoachServiceRequest.Status.PENDING
        )

        return request

    @staticmethod
    def accept_request(actor, request_id):
        request = CoachServiceRequest.objects.filter(id=request_id).first()
        if not request:
            raise NotFoundException("Request not found")
        if request.service.coach != actor:
            raise ForbiddenException("You cannot accept this request")
        if request.status != 'pending':
            raise ForbiddenException("Request is not pending")

        request.status = 'accepted'
        request.save()
        return request

    @staticmethod
    def reject_request(actor, request_id):
        request = CoachServiceRequest.objects.filter(id=request_id).first()
        if not request:
            raise NotFoundException("Request not found")
        if request.service.coach != actor:
            raise ForbiddenException("You cannot reject this request")
        if request.status != 'pending':
            raise ForbiddenException("Request is not pending")

        request.status = 'rejected'
        request.save()
        return request

    @staticmethod
    def complete_request(actor, request_id):
        request = CoachServiceRequest.objects.filter(id=request_id).first()
        if not request:
            raise NotFoundException("Request not found")
        if request.service.coach != actor:
            raise ForbiddenException("You cannot complete this request")

        request.status = 'completed'
        request.save()
        return request

    @staticmethod
    def mark_paid(request: CoachServiceRequest, transaction: PaymentTransaction):
        """
        Mark the request as paid and link the transaction
        """
        request.status = 'paid'
        request.payment = transaction
        request.save()

        # Split revenue: platform vs coach
        RevenueSplitService.create_for_coach_request(request, transaction)

        return request
