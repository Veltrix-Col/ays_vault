from pathlib import Path
from datetime import timedelta
import os, secrets, sys
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
RUNNING_TESTS=len(sys.argv)>1 and sys.argv[1]=='test'
SECRET_KEY=os.getenv('SECRET_KEY','') or (f'dev-{secrets.token_urlsafe(50)}' if DEBUG else '')
if not SECRET_KEY: raise ImproperlyConfigured('SECRET_KEY requerida')
ALLOWED_HOSTS=[x.strip() for x in os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost').split(',') if x.strip()]
CSRF_TRUSTED_ORIGINS=[x.strip() for x in os.getenv('CSRF_TRUSTED_ORIGINS','').split(',') if x.strip()]
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','django_otp','django_otp.plugins.otp_totp','axes','vault.apps.VaultConfig','soat.apps.SoatConfig','conciliacion.apps.ConciliacionConfig','integrations.apps.IntegrationsConfig','cotizacion_colectivos.apps.CotizacionColectivosConfig']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django_otp.middleware.OTPMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','axes.middleware.AxesMiddleware','vault.middleware.SecurityHeadersMiddleware','config.middleware.TrustedIntranetAccessMiddleware','vault.middleware.SecureSessionMiddleware','vault.middleware.AuditAccessMiddleware']
AUTHENTICATION_BACKENDS=['axes.backends.AxesStandaloneBackend','django.contrib.auth.backends.ModelBackend']
ROOT_URLCONF='config.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','vault.context_processors.profile','cotizacion_colectivos.context_processors.colectivos_navigation']}}]
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
TOOLS_ACCESS_MODE=os.getenv('TOOLS_ACCESS_MODE','local_public').strip().lower()
if TOOLS_ACCESS_MODE not in {'local_public','trusted_intranet'}:
    raise ImproperlyConfigured('TOOLS_ACCESS_MODE debe ser local_public o trusted_intranet')
if TOOLS_ACCESS_MODE == 'local_public' and not DEBUG and not RUNNING_TESTS:
    raise ImproperlyConfigured('TOOLS_ACCESS_MODE=local_public solo se permite con DEBUG=true')
TOOLS_DELEGATED_ACCESS_VALIDATOR=os.getenv('TOOLS_DELEGATED_ACCESS_VALIDATOR','').strip()
CSRF_COOKIE_HTTPONLY=True; SESSION_COOKIE_HTTPONLY=True; SESSION_COOKIE_SAMESITE='Lax'; CSRF_COOKIE_SAMESITE='Lax'; X_FRAME_OPTIONS='DENY'; SECURE_CONTENT_TYPE_NOSNIFF=True; SECURE_REFERRER_POLICY='same-origin'
SESSION_COOKIE_AGE=600; SESSION_SAVE_EVERY_REQUEST=True; SESSION_EXPIRE_AT_BROWSER_CLOSE=True
SESSION_INACTIVITY_SECONDS=int(os.getenv('SESSION_INACTIVITY_SECONDS','600')); SESSION_ACTIVITY_THROTTLE_SECONDS=int(os.getenv('SESSION_ACTIVITY_THROTTLE_SECONDS','60'))
REAUTH_TTL_SECONDS=int(os.getenv('REAUTH_TTL_SECONDS','300')); MFA_FAILURE_LIMIT=int(os.getenv('MFA_FAILURE_LIMIT','5'))
_configured_mfa_issuer=os.getenv('MFA_ISSUER','CardManager').strip()
# El valor histórico no es un identificador de seguridad: se normaliza al
# nombre comercial actual sin alterar secretos ni dispositivos ya enrolados.
MFA_ISSUER='CardManager' if _configured_mfa_issuer in {'','A&S Vault'} else _configured_mfa_issuer
OTP_TOTP_ISSUER=MFA_ISSUER; OTP_TOTP_THROTTLE_FACTOR=1
AXES_FAILURE_LIMIT=5; AXES_COOLOFF_TIME=timedelta(minutes=30); AXES_RESET_ON_SUCCESS=True; AXES_LOCKOUT_PARAMETERS=[['username','ip_address']]
FIELD_ENCRYPTION_KEY=os.getenv('FIELD_ENCRYPTION_KEY','')
FIELD_FINGERPRINT_KEY=os.getenv('FIELD_FINGERPRINT_KEY','')
OFFICE_START=os.getenv('OFFICE_START','07:00'); OFFICE_END=os.getenv('OFFICE_END','18:00')
ALERT_EMAIL=os.getenv('ALERT_EMAIL',''); DEFAULT_FROM_EMAIL=os.getenv('DEFAULT_FROM_EMAIL','alertas@ays.local').strip(); EMAIL_BACKEND=os.getenv('EMAIL_BACKEND','django.core.mail.backends.console.EmailBackend').strip()
ALERT_EMAIL_BACKEND=os.getenv('ALERT_EMAIL_BACKEND','console').strip().lower()
if RUNNING_TESTS:
    # La suite nunca hereda SMTP o Graph del .env local/producción.
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
    ALERT_EMAIL_BACKEND='console'
    EMAIL_CONFIGURATION_ERRORS.clear()
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
CONCILIACION_APP_URL=os.getenv('CONCILIACION_APP_URL','').strip()
CONCILIACION_MAX_UPLOAD_BYTES=int(os.getenv('CONCILIACION_MAX_UPLOAD_BYTES',str(25*1024*1024)))
REPORT_XLSX_MAX_ROWS=int(os.getenv('REPORT_XLSX_MAX_ROWS','5000'))
REPORT_PDF_MAX_ROWS=int(os.getenv('REPORT_PDF_MAX_ROWS','1000'))
COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=email_env_int('COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS',86400,300,604800)
COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=email_env_int('COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS',604800,3600,2592000)
COLECTIVOS_EXTERNAL_LINK_DAYS=email_env_int('COLECTIVOS_EXTERNAL_LINK_DAYS',15,1,30)
COLECTIVOS_EXTERNAL_LINK_MAX_DAYS=email_env_int('COLECTIVOS_EXTERNAL_LINK_MAX_DAYS',30,1,90)
COLECTIVOS_EXTERNAL_CONTACT_CHANNEL=os.getenv('COLECTIVOS_EXTERNAL_CONTACT_CHANNEL','Contacte a la persona de A&S que compartio este enlace.').strip()
COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS=email_env_int('COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS',600,60,1800)
COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS=email_env_int('COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS',5,1,10)
COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS=email_env_int('COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS',1800,300,7200)
COLECTIVOS_EXCEL_PREVIEW_TTL_SECONDS=email_env_int('COLECTIVOS_EXCEL_PREVIEW_TTL_SECONDS',1800,300,7200)
COLECTIVOS_DEADLINES_ENABLED=email_env_bool('COLECTIVOS_DEADLINES_ENABLED',True)
COLECTIVOS_DEADLINE_REMINDER_DAYS=email_env_int('COLECTIVOS_DEADLINE_REMINDER_DAYS',3,1,30)
COLECTIVOS_DEADLINE_BATCH_LIMIT=email_env_int('COLECTIVOS_DEADLINE_BATCH_LIMIT',200,1,5000)
COLECTIVOS_DEADLINE_EMAIL_ENABLED=email_env_bool('COLECTIVOS_DEADLINE_EMAIL_ENABLED',True)
COLECTIVOS_DEADLINE_INTERNAL_EMAIL=os.getenv('COLECTIVOS_DEADLINE_INTERNAL_EMAIL','').strip()
COLECTIVOS_ATTACHMENT_MAX_BYTES=email_env_int('COLECTIVOS_ATTACHMENT_MAX_BYTES',10*1024*1024,1024,25*1024*1024)
COLECTIVOS_ATTACHMENT_TOTAL_BYTES=email_env_int('COLECTIVOS_ATTACHMENT_TOTAL_BYTES',25*1024*1024,1024,100*1024*1024)
COLECTIVOS_PRIVATE_ROOT=BASE_DIR/'private_assets'/'colectivos'
COLECTIVOS_EXTERNAL_BASE_URL=os.getenv('COLECTIVOS_EXTERNAL_BASE_URL',VAULT_BASE_URL).rstrip('/')
COLECTIVOS_INTERNAL_PUBLIC_ACCESS=email_env_bool('COLECTIVOS_INTERNAL_PUBLIC_ACCESS',True)
COLECTIVOS_TECHNICAL_ACTOR_USERNAME=os.getenv('COLECTIVOS_TECHNICAL_ACTOR_USERNAME','colectivos-technical').strip()
if COLECTIVOS_INTERNAL_PUBLIC_ACCESS and not COLECTIVOS_TECHNICAL_ACTOR_USERNAME:
    raise ImproperlyConfigured('COLECTIVOS_TECHNICAL_ACTOR_USERNAME es requerido en modo publico interno.')
COLECTIVOS_ORGANIZATION_CACHE_TTL_SECONDS=email_env_int('COLECTIVOS_ORGANIZATION_CACHE_TTL_SECONDS',300,30,3600)
COLECTIVOS_METADATA_CACHE_TTL_SECONDS=email_env_int('COLECTIVOS_METADATA_CACHE_TTL_SECONDS',1800,60,86400)
COLECTIVOS_POLICY_PREPARATION_TTL_SECONDS=email_env_int('COLECTIVOS_POLICY_PREPARATION_TTL_SECONDS',600,60,3600)
COLECTIVOS_POLICY_WORKSPACE_TTL_SECONDS=email_env_int('COLECTIVOS_POLICY_WORKSPACE_TTL_SECONDS',28800,300,86400)
COLECTIVOS_GROUP_PAGE_SIZE=email_env_int('COLECTIVOS_GROUP_PAGE_SIZE',200,20,200)
COLECTIVOS_GROUP_MAX_RECORDS=email_env_int('COLECTIVOS_GROUP_MAX_RECORDS',10000,1001,50000)
REPORT_DEFAULT_MAX_DAYS=int(os.getenv('REPORT_DEFAULT_MAX_DAYS','90'))
REPORT_LARGE_EXPORT_ALERT_THRESHOLD=int(os.getenv('REPORT_LARGE_EXPORT_ALERT_THRESHOLD','1000'))
ZOHO_ENABLED=env_bool('ZOHO_ENABLED',False)
ZOHO_PUBLIC_SETUP_ENABLED=env_bool('ZOHO_PUBLIC_SETUP_ENABLED',False)
ZOHO_ACTIVE_PROFILE=os.getenv('ZOHO_ACTIVE_PROFILE','sandbox').strip().lower()
if ZOHO_ACTIVE_PROFILE not in {'sandbox','production'}:
    raise ImproperlyConfigured('ZOHO_ACTIVE_PROFILE debe ser sandbox o production.')
ZOHO_CLIENT_ID=os.getenv('ZOHO_CLIENT_ID','').strip()
ZOHO_CLIENT_SECRET=os.getenv('ZOHO_CLIENT_SECRET','')
ZOHO_REDIRECT_URI=os.getenv('ZOHO_REDIRECT_URI','http://localhost:8000/integrations/zoho/callback/').strip()
ZOHO_ACCOUNTS_BASE_URL=os.getenv('ZOHO_ACCOUNTS_BASE_URL','https://accounts.zoho.com').strip()
ZOHO_API_BASE_URL=os.getenv('ZOHO_API_BASE_URL','https://www.zohoapis.com').strip()
ZOHO_OAUTH_SCOPES=os.getenv(
    'ZOHO_OAUTH_SCOPES',
    'ZohoCRM.org.READ,ZohoCRM.settings.modules.READ,'
    'ZohoCRM.settings.fields.READ,ZohoCRM.modules.READ,ZohoCRM.coql.READ',
).strip()
ZOHO_REFRESH_TOKEN=os.getenv('ZOHO_REFRESH_TOKEN','')
ZOHO_REQUEST_TIMEOUT_SECONDS=os.getenv('ZOHO_REQUEST_TIMEOUT_SECONDS','15').strip()
ZOHO_MAX_RETRIES=os.getenv('ZOHO_MAX_RETRIES','2').strip()
ZOHO_BACKEND=os.getenv('ZOHO_BACKEND','sdk').strip().lower()
ZOHO_SDK_RESOURCE_PATH=os.getenv('ZOHO_SDK_RESOURCE_PATH','runtime/zoho_sdk').strip()
ZOHO_SDK_LOG_LEVEL=os.getenv('ZOHO_SDK_LOG_LEVEL','INFO').strip().upper()
ZOHO_PRODUCTION_ENABLED=env_bool('ZOHO_PRODUCTION_ENABLED',ZOHO_ENABLED)
ZOHO_PRODUCTION_CLIENT_ID=os.getenv('ZOHO_PRODUCTION_CLIENT_ID','').strip()
ZOHO_PRODUCTION_CLIENT_SECRET=os.getenv('ZOHO_PRODUCTION_CLIENT_SECRET','')
ZOHO_PRODUCTION_REFRESH_TOKEN=os.getenv('ZOHO_PRODUCTION_REFRESH_TOKEN','')
ZOHO_PRODUCTION_EXPECTED_ORG_ID=os.getenv('ZOHO_PRODUCTION_EXPECTED_ORG_ID','').strip()
ZOHO_PRODUCTION_ENVIRONMENT=os.getenv('ZOHO_PRODUCTION_ENVIRONMENT','production').strip().lower()
ZOHO_PRODUCTION_ACCOUNTS_BASE_URL=os.getenv('ZOHO_PRODUCTION_ACCOUNTS_BASE_URL','https://accounts.zoho.com').strip()
ZOHO_PRODUCTION_API_BASE_URL=os.getenv('ZOHO_PRODUCTION_API_BASE_URL','https://www.zohoapis.com').strip()
ZOHO_PRODUCTION_SDK_RESOURCE_PATH=os.getenv('ZOHO_PRODUCTION_SDK_RESOURCE_PATH','runtime/zoho_sdk/production').strip()
ZOHO_SANDBOX_ENABLED=env_bool('ZOHO_SANDBOX_ENABLED',False)
ZOHO_SANDBOX_CLIENT_ID=os.getenv('ZOHO_SANDBOX_CLIENT_ID','').strip()
ZOHO_SANDBOX_CLIENT_SECRET=os.getenv('ZOHO_SANDBOX_CLIENT_SECRET','')
ZOHO_SANDBOX_REFRESH_TOKEN=os.getenv('ZOHO_SANDBOX_REFRESH_TOKEN','')
ZOHO_SANDBOX_EXPECTED_ORG_ID=os.getenv('ZOHO_SANDBOX_EXPECTED_ORG_ID','').strip()
ZOHO_SANDBOX_ENVIRONMENT=os.getenv('ZOHO_SANDBOX_ENVIRONMENT','sandbox').strip().lower()
ZOHO_SANDBOX_ACCOUNTS_BASE_URL=os.getenv('ZOHO_SANDBOX_ACCOUNTS_BASE_URL','https://accounts.zoho.com').strip()
ZOHO_SANDBOX_API_BASE_URL=os.getenv('ZOHO_SANDBOX_API_BASE_URL','https://sandbox.zohoapis.com').strip()
ZOHO_SANDBOX_SDK_RESOURCE_PATH=os.getenv('ZOHO_SANDBOX_SDK_RESOURCE_PATH','runtime/zoho_sdk/sandbox').strip()
for _zoho_reserved_profile in ('QA','DEMO','FUTURE'):
    globals()[f'ZOHO_{_zoho_reserved_profile}_ENABLED']=env_bool(f'ZOHO_{_zoho_reserved_profile}_ENABLED',False)
    globals()[f'ZOHO_{_zoho_reserved_profile}_CLIENT_ID']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_CLIENT_ID','').strip()
    globals()[f'ZOHO_{_zoho_reserved_profile}_CLIENT_SECRET']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_CLIENT_SECRET','')
    globals()[f'ZOHO_{_zoho_reserved_profile}_REFRESH_TOKEN']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_REFRESH_TOKEN','')
    globals()[f'ZOHO_{_zoho_reserved_profile}_EXPECTED_ORG_ID']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_EXPECTED_ORG_ID','').strip()
    globals()[f'ZOHO_{_zoho_reserved_profile}_ENVIRONMENT']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_ENVIRONMENT',_zoho_reserved_profile.lower()).strip().lower()
    globals()[f'ZOHO_{_zoho_reserved_profile}_ACCOUNTS_BASE_URL']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_ACCOUNTS_BASE_URL','').strip()
    globals()[f'ZOHO_{_zoho_reserved_profile}_API_BASE_URL']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_API_BASE_URL','').strip()
    globals()[f'ZOHO_{_zoho_reserved_profile}_SDK_RESOURCE_PATH']=os.getenv(f'ZOHO_{_zoho_reserved_profile}_SDK_RESOURCE_PATH',f'runtime/zoho_sdk/{_zoho_reserved_profile.lower()}').strip()
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
        'integrations.zoho': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'cotizacion_colectivos': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'application_access': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}
EMAIL_PRODUCTION_ENV=APP_ENV.strip().lower() not in {'development','dev','test','testing'} and not RUNNING_TESTS
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
