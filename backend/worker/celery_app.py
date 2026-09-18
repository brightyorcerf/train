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
    # acks_late alone is not resumability. Redis redelivers an unacked task only after
    # visibility_timeout, which defaults to ONE HOUR: a worker killed mid-trace left the job in
    # FETCHING with no error and no retry — observed holding for 8 minutes with a healthy worker
    # while the console span its poller (DEMO's "never a spinner" did not hold). Two changes:
    # a lost worker's task is requeued immediately, and unacked work comes back in 90s.
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 90},
    result_backend_transport_options={"visibility_timeout": 90},
)
