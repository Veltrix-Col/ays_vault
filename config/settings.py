from pathlib import Path
from datetime import timedelta
import os, secrets
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
BASE_DIR=Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR/'.env')
EMAIL_CONFIGURATION_ERRORS=[]
def env_bool(n,d=False): return os.getenv(n,str(d)).lower() in {'1','true','yes','on'}
def email_env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None: return default
    normalized = raw.strip().lower()
    if normalized in {'1','true','yes','on'}: return True
    if normalized in {'0','false','no','off'}: return False
    EMAIL_CONFIGURATION_ERRORS.append(f'{name} debe ser un booleano valido')
    return default
def email_env_int(name, default, minimum=1, maximum=None):
    raw = os.getenv(name)
    if raw is None: return default
    try: value = int(raw)
    except (TypeError, ValueError):
        EMAIL_CONFIGURATION_ERRORS.append(f'{name} debe ser un entero valido')
        return default
    if value < minimum or (maximum is not None and value > maximum):
        EMAIL_CONFIGURATION_ERRORS.append(f'{name} esta fuera del rango permitido')
        return default
    return value
APP_ENV=os.getenv('APP_ENV','development')
DEBUG=env_bool('DEBUG',APP_ENV=='development')
SECRET_KEY=os.getenv('SECRET_KEY','') or (f'dev-{secrets.token_urlsafe(50)}' if DEBUG else '')
if not SECRET_KEY: raise ImproperlyConfigured('SECRET_KEY requerida')
ALLOWED_HOSTS=[x.strip() for x in os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost').split(',') if x.strip()]
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','django_otp','django_otp.plugins.otp_totp','axes','vault.apps.VaultConfig','soat.apps.SoatConfig']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django_otp.middleware.OTPMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','axes.middleware.AxesMiddleware','vault.middleware.SecurityHeadersMiddleware','vault.middleware.SecureSessionMiddleware','vault.middleware.AuditAccessMiddleware']
AUTHENTICATION_BACKENDS=['axes.backends.AxesStandaloneBackend','django.contrib.auth.backends.ModelBackend']
ROOT_URLCONF='config.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','vault.context_processors.profile']}}]
WSGI_APPLICATION='config.wsgi.application'
DB_ENGINE=os.getenv('DB_ENGINE','sqlite').lower()
if DB_ENGINE in {'postgres','postgresql'}:
    DATABASES={'default':{'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('DB_NAME','ays_vault'),'USER':os.getenv('DB_USER','ays_vault'),'PASSWORD':os.getenv('DB_PASSWORD',''),'HOST':os.getenv('DB_HOST','localhost'),'PORT':os.getenv('DB_PORT','5432')}}
else:
    # SQLite se usa solo en desarrollo. IMMEDIATE evita que dos escrituras
    # críticas intenten ascender simultáneamente desde una transacción diferida;
    # el timeout permite que el segundo escritor espere el commit del primero.
    DATABASES={'default':{
        'ENGINE':'django.db.backends.sqlite3',
        'NAME':BASE_DIR/'db.sqlite3',
        'OPTIONS':{'timeout':20,'transaction_mode':'IMMEDIATE'},
    }}
AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':10}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE='es-co'; TIME_ZONE='America/Bogota'; USE_I18N=True; USE_TZ=True
STATIC_URL='/static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; STATICFILES_DIRS=[BASE_DIR/'static']
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'; LOGIN_URL='login'; LOGIN_REDIRECT_URL='vault:dashboard'; LOGOUT_REDIRECT_URL='login'
CSRF_COOKIE_HTTPONLY=True; SESSION_COOKIE_HTTPONLY=True; SESSION_COOKIE_SAMESITE='Lax'; CSRF_COOKIE_SAMESITE='Lax'; X_FRAME_OPTIONS='DENY'; SECURE_CONTENT_TYPE_NOSNIFF=True; SECURE_REFERRER_POLICY='same-origin'
SESSION_COOKIE_AGE=600; SESSION_SAVE_EVERY_REQUEST=True; SESSION_EXPIRE_AT_BROWSER_CLOSE=True
SESSION_INACTIVITY_SECONDS=int(os.getenv('SESSION_INACTIVITY_SECONDS','600')); SESSION_ACTIVITY_THROTTLE_SECONDS=int(os.getenv('SESSION_ACTIVITY_THROTTLE_SECONDS','60'))
REAUTH_TTL_SECONDS=int(os.getenv('REAUTH_TTL_SECONDS','300')); MFA_FAILURE_LIMIT=int(os.getenv('MFA_FAILURE_LIMIT','5')); MFA_ISSUER=os.getenv('MFA_ISSUER','A&S Vault')
OTP_TOTP_ISSUER=MFA_ISSUER; OTP_TOTP_THROTTLE_FACTOR=1
AXES_FAILURE_LIMIT=5; AXES_COOLOFF_TIME=timedelta(minutes=30); AXES_RESET_ON_SUCCESS=True; AXES_LOCKOUT_PARAMETERS=[['username','ip_address']]
FIELD_ENCRYPTION_KEY=os.getenv('FIELD_ENCRYPTION_KEY','')
FIELD_FINGERPRINT_KEY=os.getenv('FIELD_FINGERPRINT_KEY','')
OFFICE_START=os.getenv('OFFICE_START','07:00'); OFFICE_END=os.getenv('OFFICE_END','18:00')
ALERT_EMAIL=os.getenv('ALERT_EMAIL',''); DEFAULT_FROM_EMAIL=os.getenv('DEFAULT_FROM_EMAIL','alertas@ays.local').strip(); EMAIL_BACKEND=os.getenv('EMAIL_BACKEND','django.core.mail.backends.console.EmailBackend').strip()
ALERT_EMAIL_BACKEND=os.getenv('ALERT_EMAIL_BACKEND','console').strip().lower()
ALERT_EMAIL_FROM=os.getenv('ALERT_EMAIL_FROM',DEFAULT_FROM_EMAIL)
ALERT_EMAIL_ADMIN=os.getenv('ALERT_EMAIL_ADMIN',ALERT_EMAIL)
ALERT_EMAIL_LEADER=os.getenv('ALERT_EMAIL_LEADER','')
EMAIL_HOST=os.getenv('EMAIL_HOST','').strip()
EMAIL_PORT=email_env_int('EMAIL_PORT',587,1,65535)
EMAIL_USE_TLS=email_env_bool('EMAIL_USE_TLS',True)
EMAIL_USE_SSL=email_env_bool('EMAIL_USE_SSL',False)
EMAIL_HOST_USER=os.getenv('EMAIL_HOST_USER','').strip()
EMAIL_HOST_PASSWORD=os.getenv('EMAIL_HOST_PASSWORD','')
MS_GRAPH_TENANT_ID=os.getenv('MS_GRAPH_TENANT_ID','')
MS_GRAPH_CLIENT_ID=os.getenv('MS_GRAPH_CLIENT_ID','')
MS_GRAPH_CLIENT_SECRET=os.getenv('MS_GRAPH_CLIENT_SECRET','')
MS_GRAPH_SENDER=os.getenv('MS_GRAPH_SENDER',ALERT_EMAIL_FROM)
EMAIL_TIMEOUT_SECONDS=email_env_int('EMAIL_TIMEOUT_SECONDS',10,1,120)
EMAIL_TIMEOUT=EMAIL_TIMEOUT_SECONDS
EMAIL_MAX_RETRIES=email_env_int('EMAIL_MAX_RETRIES',3,1,10)
VAULT_BASE_URL=os.getenv('VAULT_BASE_URL','http://127.0.0.1:8000').rstrip('/')
SOAT_APP_URL=os.getenv('SOAT_APP_URL','').strip()
SOAT_ZOHO_REPORT_URL=os.getenv('SOAT_ZOHO_REPORT_URL','').strip()
SOAT_MAX_UPLOAD_BYTES=int(os.getenv('SOAT_MAX_UPLOAD_BYTES',str(25*1024*1024)))
SOAT_MAX_ROWS=int(os.getenv('SOAT_MAX_ROWS','100000'))
SOAT_MAX_COLUMNS=int(os.getenv('SOAT_MAX_COLUMNS','200'))
REPORT_XLSX_MAX_ROWS=int(os.getenv('REPORT_XLSX_MAX_ROWS','5000'))
REPORT_PDF_MAX_ROWS=int(os.getenv('REPORT_PDF_MAX_ROWS','1000'))
REPORT_DEFAULT_MAX_DAYS=int(os.getenv('REPORT_DEFAULT_MAX_DAYS','90'))
REPORT_LARGE_EXPORT_ALERT_THRESHOLD=int(os.getenv('REPORT_LARGE_EXPORT_ALERT_THRESHOLD','1000'))
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'json': {'()': 'vault.logging_utils.JSONFormatter'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'json'}},
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
        'vault': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}
EMAIL_PRODUCTION_ENV=APP_ENV.strip().lower() not in {'development','dev','test','testing'}
if EMAIL_PRODUCTION_ENV:
    if EMAIL_CONFIGURATION_ERRORS: raise ImproperlyConfigured('Configuracion de correo invalida')
    if ALERT_EMAIL_BACKEND not in {'smtp','graph','microsoft_graph'}: raise ImproperlyConfigured('Backend de correo no permitido en produccion')
    if ALERT_EMAIL_BACKEND == 'smtp' and (EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend' or not all((EMAIL_HOST,EMAIL_HOST_USER,EMAIL_HOST_PASSWORD,DEFAULT_FROM_EMAIL)) or EMAIL_USE_TLS == EMAIL_USE_SSL): raise ImproperlyConfigured('Configuracion SMTP incompleta o invalida')
    if ALERT_EMAIL_BACKEND in {'graph','microsoft_graph'} and not all((MS_GRAPH_TENANT_ID,MS_GRAPH_CLIENT_ID,MS_GRAPH_CLIENT_SECRET,MS_GRAPH_SENDER)): raise ImproperlyConfigured('Configuracion Microsoft Graph incompleta')
if not DEBUG:
    if DB_ENGINE not in {'postgres','postgresql'}: raise ImproperlyConfigured('PostgreSQL es obligatorio fuera de desarrollo')
    if not FIELD_ENCRYPTION_KEY: raise ImproperlyConfigured('FIELD_ENCRYPTION_KEY requerida en producción')
    if not FIELD_FINGERPRINT_KEY: raise ImproperlyConfigured('FIELD_FINGERPRINT_KEY requerida en produccion')
    SESSION_COOKIE_SECURE=True; CSRF_COOKIE_SECURE=True; SECURE_SSL_REDIRECT=True; SECURE_HSTS_SECONDS=31536000; SECURE_HSTS_INCLUDE_SUBDOMAINS=True; SECURE_HSTS_PRELOAD=True
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')