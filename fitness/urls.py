from django.urls import path
from . import views

app_name = 'fitness'

urlpatterns = [
     #jwt authentication url's
  path('add/gym/', views.add_gym_api_jwt, name="add_gym_jwt"),
  path('add/cardio/', views.add_cardio_api_jwt, name="add_cardio_jwt"),
  path('add/sport/', views.add_sport, name="add_sport"),
  path('add/booze/', views.add_booze, name="add_booze"),
  path("api/comments/<str:workout_type>/<int:workout_id>/", views.get_comments_api, name='get_comments_api'),
  path("api/comments/", views.add_comment_api_jwt, name = 'add_comment_api_jwt'),
]
