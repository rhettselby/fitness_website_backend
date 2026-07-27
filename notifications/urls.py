from django.urls import path

from . import views

urlpatterns = [
    path('unsubscribe/<str:token>/', views.unsubscribe, name='unsubscribe'),
]
