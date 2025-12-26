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

    path('whoop/connect/', views.whoop_connect, name='whoop_connect'),
    path('whoop/callback/', views.whoop_callback, name='whoop_callback'),
    path('whoop/sync/', views.sync_whoop, name='sync_whoop'),
    path('whoop/webhook/', views.whoop_webhook, name='whoop_webhook'),
]   

