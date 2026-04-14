import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import EmailBlast
from core.views import _send_blast_from_blast_config


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send scheduled EmailBlast records whose scheduled_at time has passed."

    def handle(self, *args, **options):
        now = timezone.now()

        # Pick blasts that have a schedule and are explicitly marked as scheduled.
        blasts = EmailBlast.objects.filter(
            scheduled_at__isnull=False,
            scheduled_at__lte=now,
            status='scheduled',
        )

        if not blasts.exists():
            self.stdout.write(self.style.NOTICE("No scheduled blasts ready to send."))
            return

        for blast in blasts:
            self.stdout.write(f"Sending blast #{blast.id} '{blast.subject}'...")

            # Mark as 'sending' so concurrent runs don't duplicate work.
            blast.status = 'sending'
            blast.save(update_fields=['status'])

            try:
                sent, failed, first_error = _send_blast_from_blast_config(blast)
            except Exception as e:
                sent, failed, first_error = 0, 0, f"Unexpected error: {str(e)}"
                logger.exception(f"Blast #{blast.id} encountered an unexpected error.")

            blast.status = 'sent' if sent > 0 else 'failed'
            blast.sent_at = timezone.now()
            blast.total_sent = sent
            blast.total_failed = failed
            blast.save(update_fields=['status', 'sent_at', 'total_sent', 'total_failed'])

            if sent > 0:
                msg = f"Blast #{blast.id}: sent={sent}, failed={failed}"
                self.stdout.write(self.style.SUCCESS(msg))
                logger.info(msg)
            else:
                msg = f"Blast #{blast.id} failed for all recipients ({failed} failures)."
                if first_error:
                    msg = f"{msg} First error: {first_error}"
                self.stdout.write(self.style.ERROR(msg))
                logger.error(msg)

