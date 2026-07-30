import mimetypes

from django.http import FileResponse, Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.vpn.models import PaymentProof


class PaymentReceiptView(APIView):
    """
    Streams a receipt image through Django instead of exposing MinIO.

    Receipts are bank documents, so the storage bucket stays private and
    this is the only way to read one. Access is limited to the person who
    submitted it and to staff.

    Returns a raw file (not the usual JSON envelope) because it's consumed
    by an <img>/Image.network, not by the API client layer.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, payment_proof_id):
        try:
            proof = PaymentProof.objects.select_related(
                "subscription", "subscription__user"
            ).get(id=payment_proof_id)
        except PaymentProof.DoesNotExist:
            raise Http404

        is_owner = proof.subscription.user_id == request.user.id
        if not (is_owner or request.user.is_staff):
            # 404 rather than 403 so a non-owner can't confirm the id exists.
            raise Http404

        if not proof.receipt_image:
            raise Http404

        content_type = (
            mimetypes.guess_type(proof.receipt_image.name)[0]
            or "application/octet-stream"
        )

        # Opened lazily and streamed - receipts can be a few MB and there's
        # no reason to hold them in memory.
        file_handle = proof.receipt_image.open("rb")
        response = FileResponse(file_handle, content_type=content_type)
        response["Content-Disposition"] = (
            f'inline; filename="{proof.receipt_image.name.split("/")[-1]}"'
        )
        # Private by definition - keep it out of shared caches.
        response["Cache-Control"] = "private, max-age=300"
        return response
