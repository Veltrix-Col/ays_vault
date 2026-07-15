from pathlib import Path
from datetime import timedelta
import os, secrets
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
BASE_DIR=Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR/'.env')
def env_bool(n,d=False): return os.getenv(n,str(d)).lower() in {'1','true','yes','on'}
APP_ENV=os.getenv('APP_ENV','development')
DEBUG=env_bool('DEBUG',APP_ENV=='development')
SECRET_KEY=os.getenv('SECRET_KEY','') or (f'dev-{secrets.token_urlsafe(50)}' if DEBUG else '')
if not SECRET_KEY: raise ImproperlyConfigured('SECRET_KEY requerida')
ALLOWED_HOSTS=[x.strip() for x in os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost').split(',') if x.strip()]
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','axes','vault.apps.VaultConfig']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','axes.middleware.AxesMiddleware','vault.middleware.AuditAccessMiddleware']
AUTHENTICATION_BACKENDS=['axes.backends.AxesStandaloneBackend','django.contrib.auth.backends.ModelBackend']
ROOT_URLCONF='config.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','vault.context_processors.profile']}}]
WSGI_APPLICATION='config.wsgi.application'
if os.getenv('DB_ENGINE','sqlite') in {'postgres','postgresql'}:
    DATABASES={'default':{'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('DB_NAME','ays_vault'),'USER':os.getenv('DB_USER','ays_vault'),'PASSWORD':os.getenv('DB_PASSWORD',''),'HOST':os.getenv('DB_HOST','localhost'),'PORT':os.getenv('DB_PORT','5432')}}
else: DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'db.sqlite3'}}
AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':10}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE='es-co'; TIME_ZONE='America/Bogota'; USE_I18N=True; USE_TZ=True
STATIC_URL='/static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; STATICFILES_DIRS=[BASE_DIR/'static']
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'; LOGIN_URL='login'; LOGIN_REDIRECT_URL='vault:dashboard'; LOGOUT_REDIRECT_URL='login'
CSRF_COOKIE_HTTPONLY=True; SESSION_COOKIE_HTTPONLY=True; SESSION_COOKIE_SAMESITE='Lax'; CSRF_COOKIE_SAMESITE='Lax'; X_FRAME_OPTIONS='DENY'; SECURE_CONTENT_TYPE_NOSNIFF=True; SECURE_REFERRER_POLICY='same-origin'
SESSION_COOKIE_AGE=600; SESSION_SAVE_EVERY_REQUEST=True; SESSION_EXPIRE_AT_BROWSER_CLOSE=True
AXES_FAILURE_LIMIT=5; AXES_COOLOFF_TIME=timedelta(minutes=30); AXES_RESET_ON_SUCCESS=True; AXES_LOCKOUT_PARAMETERS=[['username','ip_address']]
FIELD_ENCRYPTION_KEY=os.getenv('FIELD_ENCRYPTION_KEY','')
OFFICE_START=os.getenv('OFFICE_START','07:00'); OFFICE_END=os.getenv('OFFICE_END','18:00')
ALERT_EMAIL=os.getenv('ALERT_EMAIL',''); DEFAULT_FROM_EMAIL=os.getenv('DEFAULT_FROM_EMAIL','alertas@ays.local'); EMAIL_BACKEND=os.getenv('EMAIL_BACKEND','django.core.mail.backends.console.EmailBackend')
if not DEBUG:
    if not FIELD_ENCRYPTION_KEY: raise ImproperlyConfigured('FIELD_ENCRYPTION_KEY requerida en producción')
    SESSION_COOKIE_SECURE=True; CSRF_COOKIE_SECURE=True; SECURE_SSL_REDIRECT=True; SECURE_HSTS_SECONDS=31536000
