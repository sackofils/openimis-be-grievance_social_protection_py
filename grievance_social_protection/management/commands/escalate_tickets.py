from django.core.management.base import BaseCommand
from django.utils import timezone
from grievance_social_protection.models import Ticket
from grievance_social_protection.services.escalation import needs_escalation, escalate_one

class Command(BaseCommand):
    help = "Escalade automatiquement les tickets en dépassement de SLA."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = Ticket.filter_queryset().exclude(
            status__in=[Ticket.TicketStatus.RESOLVED, Ticket.TicketStatus.CLOSED]
        )
        total = escalated = 0
        for t in qs.iterator():
            total += 1
            if needs_escalation(t):
                if not opts["dry_run"]:
                    if escalate_one(t, reason="due_date_passed", user=None):
                        escalated += 1
                else:
                    escalated += 1
        self.stdout.write(self.style.SUCCESS(f"checked={total} escalated={escalated}"))
