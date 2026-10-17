from django.urls import path
from . import views

app_name = 'insights'

urlpatterns = [
    path('', views.insight_history_api_jwt, name='insight_history'),
    path('latest/', views.latest_insight_api_jwt, name='latest_insight'),
]
