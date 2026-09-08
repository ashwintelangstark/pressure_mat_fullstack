"""
URL configuration for pressure_visualization project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from pressure_app import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.landing_page, name='landing_page'),
    path('login/', views.login_page, name='login_page'),
    path('home/', views.home, name='home'),
    path('logout/', views.doctor_logout, name='logout'),
    path('api/doctor/register/', views.doctor_register, name='doctor_register'),
    path('api/doctor/login/', views.doctor_login, name='doctor_login'),
    path('start/', views.start_visualization, name='start_visualization'),
    path('api/save_session/', views.save_session, name='save_session'),
    path('api/save_video/', views.save_video, name='save_video'),
    path('api/readings/', views.get_latest_readings, name='get_readings'),
    path('api/verify_patient/<str:patient_id>/', views.verify_patient, name='verify_patient'),
    path('patient/<str:patient_id>/readings/', views.get_patient_readings, name='patient_readings'),
    path('add_patient/', views.add_patient, name='add_patient'),
    path('delete_patient/<str:patient_id>/', views.delete_patient, name='delete_patient'),
    path('loading_page/', views.loading_page, name='loading_page'),

    # Serial Ports & Mat Bridge
    path('api/ports/', views.get_serial_ports, name='get_serial_ports'),

    # Game mode ("Collect the Stars") & Mat Stream
    path('api/mat/frame/', views.api_game_frame, name='api_mat_frame'),
    path('api/mat/recalibrate/', views.api_mat_recalibrate, name='api_mat_recalibrate_global'),
    path('api/mat/threshold/', views.api_game_threshold, name='api_game_threshold_global'),
    path('mat/<str:patient_id>/', views.mat_page, name='mat_page'),
    path('game/<str:patient_id>/', views.game_page, name='game_page'),
    path('api/game/<str:patient_id>/start_bridge/', views.api_start_game_bridge, name='api_start_game_bridge'),
    path('api/game/<str:patient_id>/stop_bridge/', views.api_stop_game_bridge, name='api_stop_game_bridge'),
    path('api/game/<str:patient_id>/frame/', views.api_game_frame, name='api_game_frame'),
    path('api/game/<str:patient_id>/recalibrate/', views.api_mat_recalibrate, name='api_mat_recalibrate'),
    path('api/game/<str:patient_id>/threshold/', views.api_game_threshold, name='api_game_threshold'),
    path('api/game/<str:patient_id>/save/', views.api_game_save, name='api_game_save'),
    path('api/game/<str:patient_id>/history/', views.get_patient_game_sessions, name='game_history'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
