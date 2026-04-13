from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('', views.view_groups, name='view_groups'),
    path('get_leaderboard/', views.get_leaderboard, name='get_leaderboard'),
    path('join_group/', views.join_group, name ='join_group'),
    path('create_group/', views.create_group, name="create_group"),

]
