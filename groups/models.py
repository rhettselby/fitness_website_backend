from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class FitnessGroup(models.Model):
    name = models.CharField(max_length=255, default = "Private Group")
    members = models.ManyToManyField(User, related_name = "fitness_group", blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name = "owned_groups", default = 1)
    motto = models.CharField(max_length=255, default = None, null=True)


    

    


class WeeklyScore(models.Model):
    """One user's final score for one leaderboard week.

    Written by ``groups.tasks.reset_scores`` right before it zeroes ``Profile.score``, so
    past weeks survive the Monday reset. ``week_start`` is that week's Monday as a Pacific
    date. Rank is computed when read, not stored; group history is these rows filtered by
    the group's current members.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="weekly_scores")
    week_start = models.DateField()
    score = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "week_start"], name="uniq_user_week")]
        indexes = [models.Index(fields=["week_start", "-score"])]

    def __str__(self):
        return f"{self.user.username} - week of {self.week_start} - {self.score}"
