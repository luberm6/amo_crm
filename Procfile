web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
bot: python -m bot.main
worker: celery -A app.workers.celery_app worker --loglevel=info -Q default -c 2
beat: celery -A app.workers.celery_app beat --loglevel=info
