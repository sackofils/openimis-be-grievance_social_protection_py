from datetime import timedelta, date
from typing import List, Tuple, Optional

from django.utils import timezone
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ValidationError

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from grievance_social_protection.models import Ticket, GrievanceType, EscalationWorkflow, EscalationStep
from location.models import Location, extend_allowed_locations
from django.core.cache import cache

User = get_user_model()

# Routage & SLA par défaut (en jours)
ROUTE_SENSITIVE:      List[str] = ["ETM", "DEVOPS"]
SLA_SENSITIVE:        List[int] = [1, 1]

ROUTE_NON_SENSITIVE:  List[str] = ["CGR", "AC", "RAC", "ETM", "DEVOPS"]
SLA_NON_SENSITIVE:    List[int] = [2, 2, 3, 3, 5]


# ────────────────────────────────────────────────────────────────────────────────
# Détection sensible / non sensible
# ────────────────────────────────────────────────────────────────────────────────
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower().replace(" ", "_")


def is_sensitive(t: Ticket) -> bool:
    # priorité critique
    if _norm(getattr(t, "priority", "")) in {"critique", "critical"}:
        return True
    # type sensible
    cat = _norm(getattr(t, "category", ""))
    if cat and GrievanceType.objects.filter(name__iexact=cat, is_sensitive=True).exists():
        return True
    # fallback json_ext
    je = getattr(t, "json_ext", {}) or {}
    cat_json = _norm(je.get("categories_plainte") or je.get("category"))
    return cat_json in {"cas_sensible", "cas_sensibles"}


def get_route_and_sla(ticket) -> tuple[list[str], list[int]]:
    """
    Récupère dynamiquement la route et le SLA depuis EscalationWorkflow / EscalationStep.
    1. Si un workflow est défini pour la catégorie du ticket, on l’utilise.
    2. Sinon, si la plainte est sensible, on prend le workflow sensible par défaut.
    3. Sinon, on prend le workflow non-sensible par défaut.
    4. Si rien n’est défini, on retombe sur la route hardcodée.
    """

    cache_key = f"workflow:{ticket.category or 'none'}:{'sens' if is_sensitive(ticket) else 'nonsens'}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    route: list[str] = []
    sla_days: list[int] = []

    # Recherche d’un workflow spécifique à la catégorie
    cat_slug = (getattr(ticket, "category", None) or "").strip().lower()
    wf_qs = EscalationWorkflow.objects.filter(active=True)
    if cat_slug:
        wf_qs = wf_qs.filter(category_slug=cat_slug)
    else:
        wf_qs = wf_qs.filter(category_slug__isnull=True)

    # Fallback selon sensibilité
    if not wf_qs.exists():
        wf_qs = EscalationWorkflow.objects.filter(
            active=True, is_sensitive=is_sensitive(ticket), category_slug__isnull=True
        )

    wf = wf_qs.first()

    # Construction de la route
    if wf:
        steps = EscalationStep.objects.filter(workflow=wf).order_by("order").select_related("group")
        route = [s.group.name for s in steps]
        sla_days = [s.sla_days for s in steps]

    # Défaut final si rien n’existe
    if not route:
        if is_sensitive(ticket):
            route, sla_days = ROUTE_SENSITIVE, SLA_SENSITIVE
        else:
            route, sla_days = ROUTE_NON_SENSITIVE, SLA_NON_SENSITIVE

    cache.set(cache_key, (route, sla_days), 3600)
    return route, sla_days


def next_due_date(level: int, sla_days: List[int]) -> date:
    idx = min(max(level, 0), len(sla_days) - 1)
    days = sla_days[idx]
    if settings.USE_TZ:
        today = timezone.localtime(timezone.now()).date()
    else:
        today = date.today()
    return today + timedelta(days=days)


# ────────────────────────────────────────────────────────────────────────────────
# Localisation : extraction & hiérarchie
# ────────────────────────────────────────────────────────────────────────────────
def _most_specific_location_from_ticket(t: Ticket) -> Optional[Location]:
    """
    Retourne la localité la plus précise disponible sur le ticket.
    Ordre : district -> sous_prefecture -> prefecture -> region (si présents).
    """
    for fname in ("district", "sous_prefecture", "prefecture", "region"):
        if hasattr(t, fname):
            loc = getattr(t, fname, None)
            if isinstance(loc, Location):
                return loc
    return None


def _district_from_any_location(loc: Optional[Location]) -> Optional[Location]:
    """
    Si loc est déjà un district (type 'D') -> retour direct.
    Sinon, remonte via Location.objects.parents(..., loc_type='D').
    """
    if not loc:
        return None
    if getattr(loc, "type", None) == "D":
        return loc
    parents_d = Location.objects.parents(loc.id, loc_type="D")
    return parents_d[0] if parents_d else None


def _infer_location_from_json_ext(t: Ticket) -> Optional[Location]:
    """
    Optionnel : inférer la location depuis json_ext.group_geo (codes ou libellés).
    """
    je = getattr(t, "json_ext", {}) or {}
    g = je.get("group_geo") or je.get("geo") or {}
    for key in ("district_code", "sous_prefecture_code", "prefecture_code", "region_code"):
        code = g.get(key)
        if code:
            loc = Location.objects.filter(code=str(code)).first()
            if loc:
                return loc
    for key in ("district", "sous_prefecture", "prefecture", "region"):
        name = g.get(key)
        if name:
            loc = Location.objects.filter(name__iexact=str(name)).first()
            if loc:
                return loc
    return None


def _target_location(t: Ticket) -> Optional[Location]:
    """
    1) FK du ticket (le plus précis) ; 2) sinon json_ext.group_geo.
    """
    return _most_specific_location_from_ticket(t) or _infer_location_from_json_ext(t)


# ────────────────────────────────────────────────────────────────────────────────
# Couverture utilisateur & sélection par groupe/localité
# ────────────────────────────────────────────────────────────────────────────────
def _covers_location(user: User, target_loc: Location) -> bool:
    """
    Couverture réelle openIMIS :
      allowed_ids = racines autorisées pour l'user
      extend_allowed_locations(..., strict=False) -> inclut enfants (+ parents si configuré)
    """
    try:
        allowed_ids = Location.objects.get_allowed_ids(user, strict=True)
        covered_ids = set(extend_allowed_locations(list(allowed_ids), strict=False))
        return target_loc.id in covered_ids
    except Exception:
        return False


def _choose_assignee_by_locality(role: str, t: Ticket) -> Optional[User]:
    """
    Sélectionne un utilisateur du rôle (groupe) en fonction de la localité :
      1) User du groupe relié exactement au district (tblUsersDistricts)
      2) User du groupe dont la couverture inclut la localité cible
      3) Fallback : premier user actif du groupe
    """
    role = (role or "").upper().strip()
    target_loc = _target_location(t)

    # groupe
    grp: Optional[Group] = Group.objects.filter(name=role).first()
    if not grp:
        return None
    base_qs = User.objects.filter(is_active=True, groups=grp)

    # 1) District exact
    target_district = _district_from_any_location(target_loc)
    if target_district:
        candidates = (
            base_qs.filter(userdistrict__location=target_district)
            .order_by("-last_login", "id")
            .distinct()
        )
        if candidates.exists():
            return candidates.first()

    # 2) Couverture réelle
    if target_loc:
        for u in base_qs.order_by("-last_login", "id"):
            if _covers_location(u, target_loc):
                return u

    # 3) Fallback
    return base_qs.order_by("id").first()


# ────────────────────────────────────────────────────────────────────────────────
# Assignation, escalade & SLA
# ────────────────────────────────────────────────────────────────────────────────
def get_user_for_role(role: str, t: Ticket) -> Optional[User]:
    """
    Point d’entrée unique (prise en compte règle explicite + localité).
    """
    return _choose_assignee_by_locality(role, t)


def assign_on_level(t: Ticket, level: int):
    """
    Affecte le ticket au rôle du niveau 'level' (avec filtrage par localité)
    et journalise l’évènement minimal dans escalation_log.
    """
    route, _ = get_route_and_sla(t)
    if level < 0 or level >= len(route):
        return
    role = route[level]
    user = get_user_for_role(role, t)

    if user and (not t.attending_staff or t.attending_staff_id != user.id):
        t.attending_staff = user

    log = list(getattr(t, "escalation_log", []) or [])
    log.append({
        "at": timezone.now().isoformat(),
        "assign_role": role,
        "user_id": getattr(user, "id", None),
        "location_id": getattr(_target_location(t), "id", None),
    })
    t.escalation_log = log


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
    """
    Escalade d’un cran si SLA atteint & non résolu.
    Met à jour : escalation_level, last_escalated_at, attending_staff, due_date, status, escalation_log.
    """
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

    t.status = Ticket.TicketStatus.IN_PROGRESS
    t.due_date = next_due_date(level_to, sla)

    steps = list(getattr(t, "escalation_log", []) or [])
    steps.append({
        "at": timezone.now().isoformat(),
        "from": level_from,
        "to": level_to,
        "reason": reason,
    })
    t.escalation_log = steps

    try:
        t.save(user=user)
    except TypeError:
        t.save()
    except ValidationError:
        return False
    return True


def bootstrap_escalation_fields(t: Ticket):
    """
    À appeler à la création/import :
      - max_escalation_level selon la route
      - due_date selon SLA niveau 0
      - 1ère assignation selon localité (avec règle explicite prioritaire)
    """
    route, sla = get_route_and_sla(t)
    t.max_escalation_level = len(route) - 1
    if not t.due_date:
        t.due_date = next_due_date(0, sla)
    if not t.attending_staff:
        assign_on_level(t, 0)
