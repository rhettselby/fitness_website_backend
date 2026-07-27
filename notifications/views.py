from django.core import signing
from django.shortcuts import render
from django.views.decorators.http import require_GET

from profile_page.models import Profile
from .emails import UNSUBSCRIBE_SALT


@require_GET
def unsubscribe(request, token):
    """One-click unsubscribe from all emails via a signed token (no login required).

    The token is an HMAC-signed user id produced by ``signing.dumps``; a tampered or
    malformed token fails signature verification and shows a friendly error page.
    """
    try:
        user_id = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    except signing.BadSignature:
        return render(request, 'notifications/unsubscribed.html', {'ok': False}, status=400)

    Profile.objects.filter(user_id=user_id).update(email_notifications=False)
    return render(request, 'notifications/unsubscribed.html', {'ok': True})
