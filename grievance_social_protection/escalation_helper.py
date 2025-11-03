import json
import uuid
from datetime import datetime, date
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from functools import lru_cache
from .models import EscalationWorkflow, EscalationStep
from core.models.user import Role, UserRole, InteractiveUser, User
from grievance_social_protection.models import Ticket, Comment, GrievanceType, GrievanceCategory
from django.core.exceptions import ValidationError
from typing import List, Tuple, Optional
from django.db import transaction

from location.models import Location, extend_allowed_locations

# Routage & SLA par défaut (en jours)
ROUTE_SENSITIVE:      List[str] = ["ETM", "DEVOPS"]
SLA_SENSITIVE:        List[int] = [1, 1]

ROUTE_NON_SENSITIVE:  List[str] = ["CGR", "AC", "RAC", "ETM", "DEVOPS"]
SLA_NON_SENSITIVE:    List[int] = [2, 2, 3, 3, 5]

def _as_django_user(u):
    """Normalise en django.contrib.auth.User."""
    if isinstance(u, User):
        return u
    if isinstance(u, InteractiveUser):
        return getattr(u, "user", None)
    return None

def make_json_serializable(obj):
    """
    Convertit récursivement tout objet Python en version JSON-sérialisable.
    - datetime/date -> ISO 8601
    - UUID -> str
    - dict/list -> conversion récursive
    - autre type non JSON -> str(obj)
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (uuid.UUID,)):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_json_serializable(v) for v in obj]
    # fallback: tout autre type
    return str(obj)


@staticmethod
def _load_workflow(is_sensitive: bool, category: str | None) -> EscalationWorkflow | None:
    qs = EscalationWorkflow.objects.filter(active=True)
    # Priorité: correspondance exacte de catégorie si fournie
    if category:
        wf = qs.filter(name__iexact=category).first()
        if wf:
            return wf
    # Sinon fallback sur is_sensitive (par défaut)
    return qs.filter(category_slug__isnull=True, is_sensitive=is_sensitive).first()

@staticmethod
def _get_escalation_from_db(ticket):
    """
    Retourne (steps, comment) :
      - steps: liste [(Role, sla_days), ...]
      - comment: texte pour log/debug
    Si rien en BDD => None
    """
    category = GrievanceCategory.objects.filter(name__iexact=ticket.sub_category).first()
    if category and category.workflow and len(category.workflow) > 0:
        route = category.workflow['ROUTE']
        sla = category.workflow['SLA']
        steps = []
        for i in range(len(route)):
            role = Role.objects.filter(name__iexact=route[i]).first()
            steps.append((role, sla[i]))
        return steps, f"workflow={category.name}"
    else:
        cat = ticket.category
        type = GrievanceType.objects.get(name__iexact=cat)
        is_sensitive = type.is_sensitive
        wf = _load_workflow(is_sensitive, cat or None)
        if not wf:
            return None
        steps = [(s.role, s.sla_days) for s in wf.steps.all().order_by("order")]
        return steps, f"workflow={wf.name}"

@staticmethod
def _fallback_chain(ticket):
    """Chaîne codée en dur (sécurité si pas de config)."""
    cat = ticket.category
    type = GrievanceType.objects.get(name__iexact=cat)
    if type.is_sensitive:
        names = ROUTE_SENSITIVE
    else:
        names = ROUTE_NON_SENSITIVE
    steps = []
    for name in names:
        grp = Role.objects.filter(name=name).first()
        if grp:
            steps.append((grp, 0))  # SLA=0 par défaut si fallback
    return steps, "fallback"

@staticmethod
def _next_step(ticket):
    """
    Détermine la prochaine étape d’escalade:
      - lit les steps BDD si dispo sinon fallback
      - lit json_ext.workflow.escalation_level pour connaître la position
    Retourne (role, sla_days, level, meta_label)
    """
    cfg = _get_escalation_from_db(ticket)
    if not cfg:
        steps, meta = _fallback_chain(ticket)
    else:
        steps, meta = cfg

    json_ext = getattr(ticket, "json_ext", {}) or {}
    wf = json_ext.get("workflow", {}) or {}
    level = wf.get("escalation_level", -1)
    next_index = level + 1
    if next_index >= len(steps):
        raise ValidationError("Maximum escalation level reached")
    role, sla_days = steps[next_index]
    return role, sla_days, next_index, meta

@staticmethod
def _most_specific_location_from_ticket(ticket):
    """
    Retourne la localité la plus précise disponible sur le ticket.
    Ordre : district -> sous_prefecture -> prefecture -> region.
    """
    for fname in ("location",):
        if hasattr(ticket, fname):
            loc = getattr(ticket, fname, None)
            if loc:
                return loc
    return None


@staticmethod
def _administrative_level_from_any_location(loc, type='D'):
    """Monte dans la hiérarchie pour trouver le district parent."""
    if not loc:
        return None
    if loc.type == type:
        return loc
    cur = loc
    while getattr(cur, "parent", None):
        cur = cur.parent
        if getattr(cur, "type", None) == type:
            return cur
    return None


@staticmethod
def _covers_location(user, target_loc: Location):
    """Vérifie si un utilisateur couvre la localité via LocationManager.allowed."""
    try:
        allowed_ids = Location.objects.get_allowed_ids(user)
        return target_loc.id in allowed_ids
    except Exception as e:
        return False


def _choose_assignee(role: Role, ticket):
    """
    Assigne un utilisateur du groupe en fonction de la localité du ticket.
    Stratégie :
      1) si on peut déterminer le district du ticket → chercher un user du groupe lié à ce district (UserDistrict)
      2) sinon, filtrer les users du groupe dont la couverture (allowed locations) inclut la localité du ticket
      3) fallback : premier user actif du groupe
    """
    # Récupération de tous les utilisateurs actifs associés à ce rôle
    base_qs = UserRole.objects.filter(role=role).select_related("user")
    # base_qs = User.objects.filter()

    # Localisation du ticket
    target_loc = _most_specific_location_from_ticket(ticket)

    # 1. Municipalité exact
    target_location = _administrative_level_from_any_location(target_loc, 'W')
    if target_location:
        candidates = (
            base_qs.filter(user__usermunicipality__location=target_location)
            .order_by("-user__last_login", "id")
            .distinct()
        )
        if candidates.exists():
            return _as_django_user(candidates.first().user) if candidates.first() else None

    # 1. District exact
    target_location = _administrative_level_from_any_location(target_loc, 'D')
    if target_location:
        candidates = (
            base_qs.filter(user__userdistrict__location=target_location)
            .order_by("-user__last_login", "id")
            .distinct()
        )
        if candidates.exists():
            return _as_django_user(candidates.first().user) if candidates.first() else None

    # 2. Couverture étendue
    if target_loc:
        for u in base_qs.order_by("-user__last_login", "id"):
            if _covers_location(u, target_loc):
                return _as_django_user(u.user) if u else None

    # 3. Fallback (aucune correspondance géographique)
    fallback = base_qs.order_by("id").first().user if base_qs.order_by("id").first() else None
    return _as_django_user(fallback)

def escalate_ticket(ticket: Ticket, username="Admin"):
    try:
        with transaction.atomic():
            # Détermine la prochaine étape
            target_group, sla_days, next_level, meta = _next_step(ticket)
            assignee = _choose_assignee(target_group, ticket)

            # Affectation & statut
            if hasattr(ticket, "attending_staff"):
                ticket.attending_staff = assignee
            try:
                if next_level > 0 and ticket.status in [Ticket.TicketStatus.RECEIVED, Ticket.TicketStatus.OPEN]:
                    ticket.status = Ticket.TicketStatus.IN_PROGRESS
            except Exception:
                pass

            # Due date = aujourd’hui + SLA de l’étape
            if hasattr(ticket, "due_date") and sla_days and sla_days > 0:
                ticket.due_date = timezone.now().date() + timezone.timedelta(days=sla_days)

            # Workflow JSON
            json_ext = getattr(ticket, "json_ext", {}) or {}
            wf = json_ext.get("workflow", {}) or {}
            now = timezone.now()
            history = wf.get("history", [])
            user_id = make_json_serializable(getattr(assignee, "id", None))
            fullnname = assignee.i_user.get_full_name() if assignee and assignee.i_user else ''
            history.append({
                "at": now.isoformat(),
                "by": username,
                "to_role": target_group.name if target_group else None,
                "to_user_id": user_id,
                "to_user_fullname": assignee.i_user.get_full_name() if assignee and assignee.i_user else '',
                "source": meta,  # 'workflow=<name>' ou 'fallback'
                "sla_days": sla_days,
            })
            wf.update({
                "assignee_role": target_group.name if target_group else None,
                "escalation_level": next_level,
                "last_escalated_at": now.isoformat(),
                "history": history,
            })
            json_ext["workflow"] = wf

            # conversion sécurisée :
            json_ext = make_json_serializable(json_ext)

            # Validation JSON safe
            try:
                json.dumps(json_ext)  # vérifie que c’est bien sérialisable
            except Exception as e:
                raise ValidationError({"json_ext": f"Contenu non sérialisable: {e}"})

            ticket.json_ext = json_ext

            ticket.save(username=username)
            return ticket

    except Exception as exc:
        raise Exception(exc)
