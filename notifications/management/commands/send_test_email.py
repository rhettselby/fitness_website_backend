"""Send a one-off test email to verify the SMTP configuration.

Usage:
    python manage.py send_test_email you@example.com
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email to confirm EMAIL_* settings are wired up correctly."

    def add_arguments(self, parser):
        parser.add_argument('recipient', help='Address to send the test message to')

    def handle(self, *args, **options):
        recipient = options['recipient']
        backend = settings.EMAIL_BACKEND

        self.stdout.write(f"backend    {backend}")
        self.stdout.write(f"host       {settings.EMAIL_HOST or '(unset)'}:{settings.EMAIL_PORT}")
        self.stdout.write(f"user       {settings.EMAIL_HOST_USER or '(unset)'}")
        # Length only — never print the password itself, it ends up in deploy logs.
        self.stdout.write(f"password   {'set (%d chars)' % len(settings.EMAIL_HOST_PASSWORD) if settings.EMAIL_HOST_PASSWORD else '(unset)'}")
        self.stdout.write(f"tls        {settings.EMAIL_USE_TLS}")
        self.stdout.write(f"from       {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write("")

        if 'console' in backend:
            raise CommandError(
                "EMAIL_BACKEND is the console backend, so nothing would leave this machine. "
                "Set EMAIL_HOST (and the other EMAIL_* vars) on this service and retry."
            )

        try:
            sent = send_mail(
                subject="Test email from Rhett's Fitness Community",
                message=(
                    "If you're reading this, SMTP is configured correctly and "
                    "comment notifications and monthly reminders will go out.\n"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(f"Send failed: {type(exc).__name__}: {exc}")

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent to {recipient}."))
        else:
            raise CommandError("send_mail reported 0 messages sent.")
