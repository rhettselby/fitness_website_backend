from django.contrib import admin
from .models import WeeklyScore

# Register your models here.


@admin.register(WeeklyScore)
class WeeklyScoreAdmin(admin.ModelAdmin):
    list_display = ("week_start", "user", "score")
    list_filter = ("week_start",)
