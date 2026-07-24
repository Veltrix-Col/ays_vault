from django.contrib import admin
from django.urls import include,path
from django.templatetags.static import static
from django.views.generic import RedirectView
from vault import auth_views
from vault import views as vault_views
from portal.views import public_home
urlpatterns=[path('',public_home,name='public_home'),path('soat/',include('soat.urls')),path('healthz/',vault_views.healthz,name='healthz'),path('favicon.ico',RedirectView.as_view(url=static('img/branding/favicon.ico'),permanent=True),name='favicon'),path('admin/',admin.site.urls),path('login/',auth_views.password_login,name='login'),path('login/mfa/',auth_views.mfa_verify,name='mfa_verify'),path('mfa/enroll/',auth_views.mfa_enroll,name='mfa_enroll'),path('mfa/recovery-codes/',auth_views.recovery_codes_confirm,name='recovery_codes_confirm'),path('logout/',auth_views.secure_logout,name='logout'),path('',include('vault.urls'))]
