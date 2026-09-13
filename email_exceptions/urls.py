from django.urls import path
from . import views
app_name = "email_exceptions"
urlpatterns = [path("", views.exception_list, name="list"), path("<int:pk>/", views.exception_detail, name="detail"), path("<int:pk>/ignore/", views.ignore_exception, name="ignore"), path("<int:pk>/create-task/", views.create_task, name="create_task"), path("api/inbound/", views.inbound, name="inbound")]
