from django.urls import path
from . import views

app_name = 'posts'

urlpatterns = [
   # path('', views.posts_list, name = "list"),
    path('new-post/', views.post_new, name = "new-post"),
    path('<slug:slug>', views.post_page, name = "page"),
    path('', views.workout_log, name = "list"),
    path('api/new-post/', views.post_new_api, name = "new-post"),
    path('api/<slug:slug>', views.post_page_api, name = "page"),

]



