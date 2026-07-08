import base64
import json

from django.conf import settings

try:
    import anthropic
except ImportError:
    anthropic = None


SYSTEM_PROMPT = (
    "You are assisting a human admin who manually reviews bank transfer receipts "
    "for a VPN reseller. You NEVER approve or reject a payment yourself - you only "
    "give the human reviewer a short, cautious opinion. Look at the receipt image "
    "and note anything that looks edited, inconsistent, blurry in suspicious places, "
    "or otherwise doesn't look like a normal bank app/ATM screenshot. Respond ONLY "
    "with a compact JSON object: "
    '{"verdict": "likely_genuine" | "suspicious" | "unclear", "notes": "short reason"}'
)


def analyze_payment_receipt(payment_proof):
    """
    Best-effort, optional AI pass over an uploaded receipt image.
    This NEVER approves/rejects anything by itself - it only fills in
    ai_verdict / ai_notes for the human admin to see on the review screen.
    Safe to call even if ANTHROPIC_API_KEY isn't configured (it just no-ops).
    """
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key or anthropic is None or not payment_proof.receipt_image:
        return payment_proof

    with payment_proof.receipt_image.open("rb") as f:
        image_bytes = f.read()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    media_type = "image/png" if payment_proof.receipt_image.name.lower().endswith("png") else "image/jpeg"

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        # Haiku is plenty for this and keeps per-receipt cost low; bump to
        # claude-sonnet-5 if you want a more careful read.
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": "Please review this payment receipt."},
                ],
            }
        ],
    )

    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    try:
        parsed = json.loads(text)
        verdict = parsed.get("verdict", "unclear")
        notes = parsed.get("notes", "")
    except (json.JSONDecodeError, AttributeError):
        verdict, notes = "unclear", text[:500]

    payment_proof.ai_checked = True
    payment_proof.ai_verdict = verdict
    payment_proof.ai_notes = notes
    payment_proof.save(update_fields=["ai_checked", "ai_verdict", "ai_notes"])
    return payment_proof
