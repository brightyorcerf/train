from celery import Celery

from app.core.config import settings

# Tasks arrive day 6 (§22). Day 1: the worker only has to boot and answer ping.
app = Celery("vasp", broker=settings.redis_url, backend=settings.redis_url)
