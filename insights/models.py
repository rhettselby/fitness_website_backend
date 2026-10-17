from django.contrib.auth.models import User
from django.db import models


class WeeklyInsight(models.Model):
    """One AI-written observation about one user's leaderboard week.

    Written by ``insights.tasks.generate_weekly_insight`` the Monday after the week closes.
    ``week_start`` is that week's Monday as a Pacific date, the same convention as
    ``groups.models.WeeklyScore``. ``stats`` is exactly what the model was shown, so a
    surprising sentence can be checked against the numbers behind it.
    """
    THEMES = [
        ("more_active", "More active than usual"),
        ("less_active", "Less active than usual"),
        ("rest", "Rest week"),
        ("consistency", "Consistency or streak"),
        ("new_activity", "New activity"),
        ("leaderboard", "Leaderboard finish"),
        ("first_week", "First week"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="weekly_insights")
    week_start = models.DateField()
    headline = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    theme = models.CharField(max_length=20, choices=THEMES)
    stats = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "week_start"], name="uniq_insight_user_week")]
        ordering = ["-week_start"]

    def __str__(self):
        return f"{self.user.username} - week of {self.week_start} - {self.headline}"
