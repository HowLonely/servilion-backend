"""
Django settings for core project.
"""
import os
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

SECRET_KEY = env('SECRET_KEY')

DEBUG = env.bool('DEBUG', default=False)

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

# Caddy termina HTTPS y reenvía el protocolo original por Nginx. Esto permite
# que Django genere URLs seguras y que el admin valide correctamente el origen.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=not DEBUG)
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=False)
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'corsheaders',

    'common',
    'authentication',
    'companies',
    'camps',
    'workers',
    'garments',
    'orders',
    'hospitality',
    'weighing',
    'sync',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Solo actúa en el servidor local: el histórico fuera de la ventana de 90
    # días se consulta en la nube (ver sync/proxy.py).
    'sync.proxy.EdgeHistoryProxyMiddleware',
]

ROOT_URLCONF = 'core.urls'

# Se mantiene el motor de templates únicamente porque lo requiere el Admin de
# Django (panel interno de staff). Las apps de negocio son API-only (JSON).
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


# Database
DATABASES = {
    'default': env.db('DATABASE_URL', default='postgres://postgres:postgres@db_servilion:5432/postgres')
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'authentication.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'es-cl'
TIME_ZONE = 'America/Santiago'
USE_I18N = True
USE_TZ = True


# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'


# --- Redis / Celery ---
REDIS_URL = env('REDIS_URL', default='redis://redis_servilion:6379/0')
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default=REDIS_URL)
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
    }
} if REDIS_URL else {
    # El servidor local de planta no levanta Redis: no corre Celery y el único
    # uso de caché (reportería) no se sirve desde ahí.
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
}


# --- Nodo: nube o servidor local de planta (ver common/node.py y sync/) ---
SERVILION_NODE = env('SERVILION_NODE', default='cloud')
# Pesaje, digitalización, empaque y despachos. Por defecto solo en el servidor
# local; en desarrollo se puede habilitar en un backend "cloud" con la variable.
ALLOW_PLANT_OPERATIONS = env.bool('ALLOW_PLANT_OPERATIONS', default=SERVILION_NODE == 'edge')

# Servidor local: a qué nube se sincroniza y con qué credencial de nodo.
SYNC_CLOUD_URL = env('SYNC_CLOUD_URL', default='').rstrip('/')
SYNC_NODE_TOKEN = env('SYNC_NODE_TOKEN', default='')
# Primer ID que usa el servidor local en las tablas sincronizadas. La nube
# numera por debajo y el servidor local por encima, así una fila conserva el
# mismo ID en los dos lados y la terminal, la web y la app hablan de la misma.
SYNC_EDGE_ID_START = env.int('SYNC_EDGE_ID_START', default=1_000_000_000_000)
# Días de guías y pesajes que el servidor local conserva. Lo anterior se
# consulta en la nube cuando hay internet.
SYNC_LOCAL_RETENTION_DAYS = env.int('SYNC_LOCAL_RETENTION_DAYS', default=90)
SYNC_PULL_INTERVAL_SECONDS = env.int('SYNC_PULL_INTERVAL_SECONDS', default=5)
SYNC_HTTP_TIMEOUT_SECONDS = env.int('SYNC_HTTP_TIMEOUT_SECONDS', default=20)


# --- CORS (panel Next.js) ---
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])


# --- JWT (autenticación stateless para Web y App móvil) ---
JWT_SECRET = env('JWT_SECRET', default=SECRET_KEY)
JWT_ALGORITHM = 'HS256'
JWT_ACCESS_TOKEN_LIFETIME = timedelta(minutes=env.int('JWT_ACCESS_TOKEN_LIFETIME_MINUTES', default=60))
JWT_REFRESH_TOKEN_LIFETIME = timedelta(days=env.int('JWT_REFRESH_TOKEN_LIFETIME_DAYS', default=30))


# --- AWS S3 (almacenamiento de medios; el backend nunca guarda archivos localmente) ---
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY', default='')
AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='')
AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='us-east-1')
AWS_S3_PRESIGNED_URL_EXPIRATION = 300  # segundos
