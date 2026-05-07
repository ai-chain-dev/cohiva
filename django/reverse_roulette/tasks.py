from celery import shared_task

from . import services


@shared_task(bind=True, ignore_result=True, name="reverse_roulette.sync_donations")
def sync_donations_task(self):  # noqa: D401 — celery convention
    """Periodic Celery task: pull pending donations and process them."""
    return services.sync_once(dry_run=False)
