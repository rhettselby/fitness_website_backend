from django.urls import path
from . import views

app_name = 'profile_page'

urlpatterns = [
    path('api/', views.viewpage_api, name = 'profile_api'),  # Added trailing slash
    path('api/edit_profile/', views.editprofile_api, name = 'edit_profile_api'),
    path('', views.viewpage, name = 'profile'),
    path('edit_profile/', views.editprofile, name = 'edit_profile'),

]