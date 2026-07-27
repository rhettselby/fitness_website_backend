"""Shared helper for sending plain-text notification emails.

Uses Django's built-in ``send_mail`` over whatever ``EMAIL_BACKEND`` is configured
(SMTP in production, console backend locally). Every email gets an unsubscribe link
built from a signed token, so no per-user token needs to be stored in the database.
"""

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

# Salt namespaces the unsubscribe signature so these tokens can't be reused elsewhere.
UNSUBSCRIBE_SALT = "unsubscribe"


def build_unsubscribe_url(user):
    """Return an absolute, HMAC-signed one-click unsubscribe URL for ``user``."""
    token = signing.dumps(user.id, salt=UNSUBSCRIBE_SALT)
    return f"{settings.SITE_URL.rstrip('/')}{reverse('unsubscribe', args=[token])}"


def send_templated_email(user, subject, template_name, context=None):
    """Render ``notifications/emails/{template_name}.txt`` and email it to ``user``.

    ``username``, ``site_url`` and ``unsubscribe_url`` are always available to the
    template. Returns True if an email was sent, False if the user has no address.
    """
    if not user.email:
        return False

    ctx = {
        "username": user.username,
        "site_url": settings.SITE_URL.rstrip('/'),
        "unsubscribe_url": build_unsubscribe_url(user),
    }
    if context:
        ctx.update(context)

    body = render_to_string(f"notifications/emails/{template_name}.txt", ctx)
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )
    return True
