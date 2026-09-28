import json
from datetime import timedelta
from zoneinfo import ZoneInfo

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from fitness.models import Cardio, Gym, Sport
from profile_page.models import Profile
from .models import WeeklyScore

PACIFIC = ZoneInfo("America/Los_Angeles")


def previous_monday_pacific(now=None):
    """Monday (as a Pacific date) of the week that has just ended.

    Beat fires reset_scores early on Monday morning Pacific time, so "yesterday" always
    falls inside the week being closed. Using the Pacific date rather than the cron tick
    keeps the label right when DST moves the 08:00 UTC tick to 1am, or the task runs late.
    """
    yesterday = (now or timezone.now()).astimezone(PACIFIC).date() - timedelta(days=1)
    return yesterday - timedelta(days=yesterday.weekday())


@shared_task
def reset_scores():
    """Archive every non-zero score as a WeeklyScore row, then subtract it from the profile.

    One transaction, so a score can never be reset without being saved first. Subtracting
    (instead of setting 0) keeps points that land mid-task for the new week. The early
    return makes a Beat retry or double-fire a no-op rather than archiving the new week's
    first points under last week's date.
    """
    week_start = previous_monday_pacific()
    with transaction.atomic():
        if WeeklyScore.objects.filter(week_start=week_start).exists():
            print(f"Week of {week_start} already archived, skipping reset")
            return

        profiles = list(Profile.objects.select_for_update().filter(score__gt=0))
        WeeklyScore.objects.bulk_create(
            [WeeklyScore(user_id=p.user_id, week_start=week_start, score=p.score) for p in profiles]
        )
        for p in profiles:
            Profile.objects.filter(pk=p.pk).update(score=F("score") - p.score)

    print(f"New week: archived {len(profiles)} scores for the week of {week_start} and reset them to 0")

import anthropic


# Cloudinary serves a resized copy straight from the delivery URL, so the API can be
# pointed at a downscaled image without this worker ever downloading it. Anything that
# isn't a Cloudinary delivery URL is passed through untouched.
def downscaled(image_url: str) -> str:
    marker = '/image/upload/'
    head, sep, tail = image_url.partition(marker)
    if not sep:
        return image_url
    return f"{head}{marker}w_1024,h_1024,c_limit,q_80/{tail}"


# Workout types a photo can earn the bonus for. Booze has no exercise to check against.
VERIFIABLE_WORKOUTS = {'cardio': Cardio, 'gym': Gym, 'sport': Sport}
VERIFICATION_BONUS = 50

# Structured output, so the verdict is a boolean rather than whatever the reply happens to
# start with ("**Yes**", "Verdict: yes", ...).
VERDICT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "verified": {"type": "boolean"},
            "reasoning": {"type": "string"},
        },
        "required": ["verified", "reasoning"],
        "additionalProperties": False,
    },
}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def verify_workout_image(self, image_url: str, exercise: str, workout_type: str, workout_id: int, user_id: int) -> bool:
    print("image verification initiated")
    model = VERIFIABLE_WORKOUTS.get(workout_type)
    if model is None:
        print(f"No image verification for {workout_type} workouts")
        return False
    # A replaced photo re-queues this task, but the bonus is once per workout, so skip the API call.
    if model.objects.filter(id=workout_id, verified=True).exists():
        print(f"{workout_type} {workout_id} is already verified, skipping")
        return True

    try:
        # image_url is the public Cloudinary URL, which the API fetches itself.
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=512,
            output_config={"format": VERDICT_FORMAT},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": downscaled(image_url)}},
                    {"type": "text", "text": f"""
                        Does this image provide at least minor evidence that the person in the photo had
                        been performing {exercise}? Evidence could include the location, other people, signs of activity or anything that
                        might indicate the given workout. Please be somewhat lenient, set verified to your decision and give a brief reasoning.
                     """}
                ]
            }]
        )
    # Broken credentials break every workout, not just this one. Raise so the task is
    # recorded as failed with a traceback rather than disappearing into a quiet False.
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        print(f"Image verification is misconfigured, not retrying: {exc}")
        raise
    # Transient — the same request may well succeed on another attempt.
    except anthropic.RateLimitError as exc:
        print(f"Rate limited by the Claude API, retrying: {exc}")
        raise self.retry(exc=exc)
    except anthropic.APIConnectionError as exc:
        print(f"Could not reach the Claude API, retrying: {exc}")
        raise self.retry(exc=exc)
    # Permanent and specific to this request: a malformed call, an unknown model, or an
    # image URL Claude couldn't fetch. Another attempt would fail identically.
    except (anthropic.BadRequestError, anthropic.NotFoundError) as exc:
        print(f"Claude rejected the verification request, not retrying: {exc}")
        return False
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            print(f"Claude API server error ({exc.status_code}), retrying: {exc}")
            raise self.retry(exc=exc)
        print(f"Claude API error ({exc.status_code}), not retrying: {exc}")
        return False
    except Exception as exc:
        # A bug or a missing setting in this task. Retrying buries it for 90 seconds and
        # then loses it entirely, so let it surface instead.
        print(f"Image verification errored before reaching the API, not retrying: {exc!r}")
        raise

    # A refusal or a max_tokens cut-off has no complete verdict to read.
    if response.stop_reason != "end_turn":
        print(f"Image verification ended with stop_reason={response.stop_reason}, not verifying")
        return False
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError:
        print(f"Image verification returned unparseable output: {text!r}")
        return False
    print(f"Image verification result: {verdict}")

    if not verdict.get("verified"):
        return False

    # Conditional F() updates rather than read-modify-save: the verified=False filter lets only
    # one task award the bonus even if two race, and F() doesn't overwrite points the profile
    # gains (or reset_scores removes) while this runs.
    with transaction.atomic():
        awarded = model.objects.filter(id=workout_id, verified=False).update(
            verified=True, score=F("score") + VERIFICATION_BONUS
        )
        if awarded:
            Profile.objects.filter(user_id=user_id).update(score=F("score") + VERIFICATION_BONUS)
    return True
    

