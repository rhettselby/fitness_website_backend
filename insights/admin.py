from django.contrib import admin
from .models import WeeklyInsight


@admin.register(WeeklyInsight)
class WeeklyInsightAdmin(admin.ModelAdmin):
    list_display = ("week_start", "user", "theme", "headline")
    list_filter = ("week_start", "theme")
