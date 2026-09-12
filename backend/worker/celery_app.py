from celery import Celery

from app.core.config import settings

# BFS runs as one chord per hop (§9.2); tasks live in app.trace.tasks.
app = Celery("vasp", broker=settings.redis_url, backend=settings.redis_url,
             include=["app.trace.tasks"])
app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    task_acks_late=True,              # a killed worker's level is re-run, not lost (§12 idempotency)
    worker_prefetch_multiplier=1,     # fan-out is bounded by the provider rate, not by prefetch
    result_expires=86400,
)
