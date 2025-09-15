from datetime import timedelta, date
from typing import List, Tuple, Optional
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.core.exceptions import ValidationError
from grievance_social_protection.models import Ticket, GrievanceType

from django.conf import settings

User = get_user_model()

ROUTE_SENSITIVE:  list[str] = ["ETM", "DEVOPS"]
SLA_SENSITIVE:    list[int] = [1, 1]  # jours / niveau

ROUTE_NON_SENSITIVE: list[str] = ["CGR", "AC", "RAC", "ETM", "DEVOPS"]
SLA_NON_SENSITIVE:   list[int] = [2, 2, 3, 3, 5]

# ————————————————————————————————————————————————————————————————
# Détection sensible / non sensible
# ————————————————————————————————————————————————————————————————
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower().replace(" ", "_")

def is_sensitive(t: Ticket) -> bool:
    # priorité « critique » (ou « critical ») → sensible
    if _norm(getattr(t, "priority", "")) in ("critique", "Critical"):
        return True
    # catégorie contenant "cas_sensible(s)"
    cat = _norm(getattr(t, "category", ""))
    types = GrievanceType.objects.filter(name__iexact=cat, is_sensitive=True)
    if len(types) > 0 :
        return True
    # fallback: json_ext
    je = getattr(t, "json_ext", {}) or {}
    cat = _norm(je.get("categories_plainte") or je.get("category"))
    return cat in ("cas_sensible", "cas_sensibles")

def get_route_and_sla(t: Ticket) -> Tuple[List[str], List[int]]:
    return (ROUTE_SENSITIVE, SLA_SENSITIVE) if is_sensitive(t) else (ROUTE_NON_SENSITIVE, SLA_NON_SENSITIVE)

def next_due_date(level: int, sla_days: List[int]) -> date:
    idx = min(max(level, 0), len(sla_days) - 1)
    days = sla_days[idx]
    if settings.USE_TZ:
        today = timezone.localtime(timezone.now()).date()  # datetime conscient du TZ -> ok
    else:
        today = date.today()  # pas de TZ -> utiliser la date système
    return today + timedelta(days=days)

# ————————————————————————————————————————————————————————————————
# Résolution d’assignataire selon le rôle & le contexte
# ————————————————————————————————————————————————————————————————
def get_user_for_role(role: str, t: Ticket) -> Optional[User]:
    """
    Stratégie simple (modifiable) :
    1) Cherche un Group exactement nommé <role> (ex. 'CGR', 'AC', 'RAC', 'ETM', 'DEVOPS')
    2) Si présent, prend le premier user actif du groupe
    3) Sinon, None (on laisse attending_staff vide)
    Tu peux raffiner par géographie: ex. groupes 'CGR_<code_sp>', etc.
    """
    try:
        grp = Group.objects.filter(name=role).first()
        if not grp:
            return None
        return grp.user_set.filter(is_active=True).order_by("id").first()
    except Exception:
        return None

def assign_on_level(t: Ticket, level: int):
    route, _ = get_route_and_sla(t)
    if level < 0 or level >= len(route):
        return
    role = route[level]
    user = get_user_for_role(role, t)
    if user and (not t.attending_staff or t.attending_staff_id != user.id):
        t.attending_staff = user
    # trace minimale (optionnel)
    log = list(t.escalation_log or [])
    log.append({"at": timezone.now().isoformat(), "assign_role": role, "user_id": getattr(user, "id", None)})
    t.escalation_log = log

# ————————————————————————————————————————————————————————————————
# Conditions d’escalade & exécution
# ————————————————————————————————————————————————————————————————
def _is_resolved(t: Ticket) -> bool:
    return t.status in (Ticket.TicketStatus.RESOLVED, Ticket.TicketStatus.CLOSED)

def _breached(t: Ticket) -> bool:
    return bool(t.due_date and t.due_date < timezone.localdate())

def needs_escalation(t: Ticket) -> bool:
    if _is_resolved(t):
        return False
    route, _ = get_route_and_sla(t)
    if (t.escalation_level or 0) >= (t.max_escalation_level or (len(route) - 1)):
        return False
    return _breached(t)

@transaction.atomic
def escalate_one(t: Ticket, *, reason="SLA breach", user=None) -> bool:
    if not needs_escalation(t):
        return False

    route, sla = get_route_and_sla(t)
    level_from = t.escalation_level or 0
    level_to = level_from + 1
    if level_to > (t.max_escalation_level or (len(route) - 1)):
        return False

    t.escalation_level = level_to
    t.last_escalated_at = timezone.now()
    assign_on_level(t, level_to)

    # reste en In Progress si pas résolu
    t.status = Ticket.TicketStatus.IN_PROGRESS
    t.due_date = next_due_date(level_to, sla)

    steps = list(t.escalation_log or [])
    steps.append({"at": timezone.now().isoformat(), "from": level_from, "to": level_to, "reason": reason})
    t.escalation_log = steps

    try:
        t.save(user=user)
    except TypeError:
        t.save()
    except ValidationError:
        return False
    return True

def bootstrap_escalation_fields(t: Ticket):
    """À appeler lors de la création/import : initialise max_escalation_level, due_date et première assignation."""
    route, sla = get_route_and_sla(t)
    t.max_escalation_level = len(route) - 1
    if not t.due_date:
        t.due_date = next_due_date(0, sla)
    if not t.attending_staff:  # première affectation
        assign_on_level(t, 0)
