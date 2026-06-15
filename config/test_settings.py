from config.settings import *

# Use fast in-memory SQLite so tests need no Postgres container
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Use in-memory cache so tests need no Redis container.
# cache_manager tests mock the cache object directly to simulate lock behaviour.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

# Suppress logging noise during tests
LOGGING = {}
