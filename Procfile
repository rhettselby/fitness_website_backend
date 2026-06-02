
web: python manage.py migrate --noinput && gunicorn myBackend.wsgi --bind 0.0.0.0:$PORT --timeout 120 --workers 1
worker: celery -A myBackend worker --beat --loglevel=info --concurrency 2