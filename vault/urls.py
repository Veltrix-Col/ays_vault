from django.urls import path
from . import views
app_name='vault'
urlpatterns=[path('',views.dashboard,name='dashboard'),path('vault/cards/',views.card_list,name='card_list'),path('vault/cards/new/',views.card_create,name='card_create'),path('vault/cards/<int:pk>/',views.card_detail,name='card_detail'),path('vault/cards/<int:pk>/reveal/',views.reveal,name='reveal'),path('vault/cards/<int:pk>/copy-event/',views.copy_event,name='copy_event'),path('vault/audit/',views.audit_list,name='audit')]
