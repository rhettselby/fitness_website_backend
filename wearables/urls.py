from django.urls import path
from . import views

app_name = 'wearables'

urlpatterns = [
    path('oura/connect/', views.oura_connect, name='oura_connect'),
    path('oura/callback/', views.oura_callback, name='oura_callback'),
    path('oura/sync/', views.sync_oura, name='sync_oura'),
    path('oura/webhook/', views.oura_webhook, name='oura_webhook'),

    path('strava/connect/', views.strava_connect, name='strava_connect'),
    path('strava/callback/', views.strava_callback, name='strava_callback'),
    path('strava/sync/', views.sync_strava, name='sync_strava'),
    path('strava/webhook/', views.strava_webhook, name='strava_webhook'),
]   