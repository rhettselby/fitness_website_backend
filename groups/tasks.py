from celery import shared_task
from profile_page.models import Profile


@shared_task
def reset_scores():
    profiles = Profile.objects.all().update(score=0)
    print(f"New week: all ({profiles}) scores reset to 0")