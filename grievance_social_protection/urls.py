from django.urls import path
from . import views

urlpatterns = [
    path("grievance/export-selected-tickets/", views.ExportSelectedTicketsAPIView.as_view(), name="export-selected-tickets"),
    path("grievance/upload_death_dossier/", views.upload_death_dossier, name="upload_death_dossier"),
    path("grievance/download/export/<str:filename>/", views.download_export, name="download_export")
]
