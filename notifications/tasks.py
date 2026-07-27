"""Celery tasks for email notifications.

- ``send_comment_notification``: emails a workout owner when someone comments,
  throttled to at most one email per recipient per 7 days.
- ``send_monthly_reminders`` / ``send_monthly_reminder_email``: fan-out monthly
  reminder to every opted-in user with an email address.
"""

from datetime import timedelta

from celery import shared_task
from django.contrib.auth.models import User
from django.utils import timezone

from fitness.models import Comment
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
        return

    if not owner.email:
        return

    profile = getattr(owner, 'profile', None)
    if profile is None or not profile.email_notifications:
        return

    # Weekly throttle: skip if we already emailed this user within the cooldown window.
    if profile.last_comment_email_at and \
            timezone.now() - profile.last_comment_email_at < COMMENT_EMAIL_COOLDOWN:
        return

    text = comment.text or ""
    preview = text if len(text) <= 150 else text[:150].rstrip() + "…"

    send_templated_email(
        owner,
        subject=f"{commenter.username} commented on your workout",
        template_name="comment_notification",
        context={
            "commenter": commenter.username,
            "workout_type": getattr(workout, 'workout_type', 'workout'),
            "comment_text": preview,
        },
    )

    profile.last_comment_email_at = timezone.now()
    profile.save(update_fields=["last_comment_email_at"])


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
