"""Celery tasks for email notifications.

- ``send_comment_notification``: emails a workout owner when someone comments,
  throttled to at most one email per recipient per 7 days.
- ``send_monthly_reminders`` / ``send_monthly_reminder_email``: fan-out monthly
  reminder to every opted-in user with an email address.
- ``send_wearable_reconnect_email``: tells a user their Whoop/Oura/Strava link was
  switched off (the provider kept rejecting our refresh token) and must be re-linked.
"""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from fitness.models import Comment
from wearables.models import WearableConnection
from .emails import send_templated_email

# At most one comment-notification email per recipient per week ("not annoying").
COMMENT_EMAIL_COOLDOWN = timedelta(days=7)


@shared_task
def send_comment_notification(comment_id):
    """Notify a workout's owner that someone commented on it (weekly-throttled)."""
    try:
        comment = Comment.objects.select_related('user').get(id=comment_id)
    except Comment.DoesNotExist:
        print(f"Comment notification: comment {comment_id} not found")
        return

    workout = comment.workout  # generic FK -> Cardio / Gym / Sport / Booze instance
    if workout is None:
        print(f"Comment notification: workout for comment {comment_id} not found")
        return

    owner = workout.user
    commenter = comment.user

    # Never email someone about their own comment.
    if owner.id == commenter.id:
        print(f"[comment-email] skip: {owner.username} commented on their own workout (comment {comment_id})")
        return

    if not owner.email:
        print(f"[comment-email] skip: owner {owner.username} has no email address")
        return

    profile = getattr(owner, 'profile', None)
    if profile is None:
        print(f"[comment-email] skip: owner {owner.username} has no profile")
        return
    if not profile.email_notifications:
        print(f"[comment-email] skip: {owner.username} is unsubscribed (email_notifications=False)")
        return

    # Weekly throttle: skip if we already emailed this user within the cooldown window.
    if profile.last_comment_email_at and \
            timezone.now() - profile.last_comment_email_at < COMMENT_EMAIL_COOLDOWN:
        print(f"[comment-email] skip: throttled — last emailed {owner.username} at {profile.last_comment_email_at}")
        return

    text = comment.text or ""
    preview = text if len(text) <= 150 else text[:150].rstrip() + "…"

    sent = send_templated_email(
        owner,
        subject=f"{commenter.username} commented on your workout",
        template_name="comment_notification",
        context={
            "commenter": commenter.username,
            "workout_type": getattr(workout, 'workout_type', 'workout'),
            "comment_text": preview,
        },
    )

    if sent:
        profile.last_comment_email_at = timezone.now()
        profile.save(update_fields=["last_comment_email_at"])
        print(f"[comment-email] sent to {owner.email} for comment {comment_id}")
    else:
        print(f"[comment-email] send skipped: {owner.username} has no email (send_templated_email returned False)")


@shared_task
def send_monthly_reminders():
    """Dispatch a monthly reminder email to every opted-in user with an address.

    Fans out one task per user (mirrors ``wearables.tasks.sync_all_wearables``) so a
    single failure can't sink the whole batch.
    """
    user_ids = list(
        User.objects
        .filter(profile__email_notifications=True)
        .exclude(email='')
        .exclude(email__isnull=True)
        .values_list('id', flat=True)
    )
    print(f"Monthly reminder: dispatching to {len(user_ids)} users")
    for uid in user_ids:
        send_monthly_reminder_email.delay(uid)


@shared_task
def send_monthly_reminder_email(user_id):
    """Send the monthly log-your-workouts reminder to a single user."""
    try:
        user = User.objects.select_related('profile').get(id=user_id)
    except User.DoesNotExist:
        return

    if not user.email:
        return

    profile = getattr(user, 'profile', None)
    if profile is None or not profile.email_notifications:
        return

    send_templated_email(
        user,
        subject="Time to log your workouts 💪",
        template_name="monthly_reminder",
    )


@shared_task
def send_wearable_reconnect_email(user_id, device_type):
    """Tell a user their wearable link was switched off and needs to be re-linked.

    Queued by ``wearables.views.ensure_fresh_token`` when a provider has definitively
    rejected our refresh token several times in a row. Without this the connection used
    to go silent and nobody found out for days.
    """
    try:
        user = User.objects.select_related('profile').get(id=user_id)
    except User.DoesNotExist:
        return

    provider = dict(WearableConnection.DEVICE_CHOICES).get(device_type, device_type.title())

    if not user.email:
        print(f"[reconnect-email] skip: {user.username} ({provider}) has no email address")
        return
    profile = getattr(user, 'profile', None)
    if profile is None or not profile.email_notifications:
        print(f"[reconnect-email] skip: {user.username} ({provider}) is unsubscribed")
        return

    send_templated_email(
        user,
        subject=f"Your {provider} connection needs to be re-linked",
        template_name="wearable_reconnect",
        context={
            "provider": provider,
            "connect_url": f"{settings.SITE_URL.rstrip('/')}/connect",
        },
    )
    print(f"[reconnect-email] sent to {user.email} for {provider}")
