from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from apps.account.services.phone import normalize_phone

User = get_user_model()


class MultiIdentifierBackend(ModelBackend):
    """
    Signs a user in by email, phone number or username - whichever they
    typed - against their password.

    USERNAME_FIELD is an internal opaque id that nobody ever types, so the
    default backend would never match anything. This resolves the real
    identifier instead.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get("identifier")
        if not identifier or not password:
            return None

        identifier = identifier.strip()
        lookup = Q(email__iexact=identifier) | Q(username__iexact=identifier)

        # Only treat it as a phone number if it actually parses as one,
        # otherwise a username of "0912..." would be rewritten into E.164
        # and never match.
        normalised_phone = None
        try:
            normalised_phone = normalize_phone(identifier)
            lookup |= Q(phone_number=normalised_phone)
        except Exception:
            pass

        user = User.objects.filter(lookup).first()
        if user is None:
            # Same cost as a real check, so response timing doesn't reveal
            # whether the account exists.
            User().set_password(password)
            return None

        if not user.check_password(password) or not self.user_can_authenticate(user):
            return None

        # A phone number is only a credential once its owner has proven it.
        # Skipping this would let someone register a number they don't own,
        # set a password, and sign in as the real owner later.
        #
        # Compared against the normalised form, not the raw input - the
        # stored value is E.164 while people type 09xxxxxxxxx, so a literal
        # comparison would never match and the check would never fire.
        signed_in_by_phone = (
            normalised_phone is not None and user.phone_number == normalised_phone
        )
        if signed_in_by_phone and not user.is_phone_verified:
            return None

        return user
