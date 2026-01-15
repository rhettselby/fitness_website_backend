
web: python manage.py migrate --noinput && gunicorn myBackend.wsgi --bind 0.0.0.0:$PORT --timeout 120 --workers 2
worker: celery -A myBackend worker --loglevel=info
beat: celery -A myBackend beat --loglevel=info