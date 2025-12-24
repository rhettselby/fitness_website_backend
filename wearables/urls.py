from django.urls import path
from . import views

app_name = 'wearables'

urlpatterns = [
    path('oura/connect/', views.oura_connect, name='oura_connect'),
    path('oura/callback/', views.oura_callback, name='oura_callback'),
    path('oura/sync/', views.sync_oura, name='sync_oura'),
]