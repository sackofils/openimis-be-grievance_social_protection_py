from django.core.exceptions import ValidationError
from grievance_social_protection.escalation_helper import escalate_ticket
from grievance_social_protection.models import Ticket

def bootstrap_escalation_fields(t: Ticket):
    """
    À appeler à la création/import :
      - max_escalation_level selon la route
      - due_date selon SLA niveau 0
      - 1ère assignation selon localité (avec règle explicite prioritaire)
    """
    if not t.attending_staff:
        t = escalate_ticket(t)
