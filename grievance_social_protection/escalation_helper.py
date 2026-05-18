import json
import uuid
import logging
from datetime import datetime, date
from typing import List

from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction

from .models import EscalationWorkflow, EscalationStep
from core.models.user import Role, UserRole, InteractiveUser, User
from grievance_social_protection.models import Ticket, Comment, GrievanceType, GrievanceCategory
from location.models import Location, extend_allowed_locations

logger = logging.getLogger(__name__)

# Routage & SLA par défaut (en jours)
ROUTE_SENSITIVE: List[str] = ["ETM", "DEVOPS"]
SLA_SENSITIVE: List[int] = [1, 1]

ROUTE_NON_SENSITIVE: List[str] = ["CGR", "AC", "RAC", "ETM", "DEVOPS"]
SLA_NON_SENSITIVE: List[int] = [2, 2, 3, 3, 5]


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
    return str(obj)


def _normalize_email(value):
    if not value:
        return None
    email = str(value).strip()
    return email or None


def _get_user_full_name_safe(user):
    if not user:
        return ""

    try:
        if hasattr(user, "i_user") and user.i_user:
            full_name = user.i_user.get_full_name()
            if full_name:
                return full_name
    except Exception:
        pass

    try:
        full_name = user.get_full_name()
        if full_name:
            return full_name
    except Exception:
        pass

    return getattr(user, "username", "") or getattr(user, "email", "") or "Utilisateur"


def _get_user_email_safe(user):
    if not user:
        return None

    email = _normalize_email(getattr(user, "email", None))
    if email:
        return email

    try:
        if hasattr(user, "i_user") and user.i_user:
            email = _normalize_email(getattr(user.i_user, "email", None))
            if email:
                return email
    except Exception:
        pass

    return None


def _is_sensitive_ticket(ticket) -> bool:
    category = getattr(ticket, "category", None)
    if not category:
        return False

    try:
        grievance_type = GrievanceType.objects.get(name__iexact=category)
        return bool(grievance_type.is_sensitive)
    except GrievanceType.DoesNotExist:
        return False
    except Exception:
        logger.exception(
            "Erreur lors de la detection de sensibilite du ticket %s",
            getattr(ticket, "id", None),
        )
        return False


def _load_workflow(is_sensitive: bool, category: str | None) -> EscalationWorkflow | None:
    qs = EscalationWorkflow.objects.filter(active=True)
    if category:
        wf = qs.filter(name__iexact=category).first()
        if wf:
            return wf
    return qs.filter(category_slug__isnull=True, is_sensitive=is_sensitive).first()


def _get_escalation_from_db(ticket):
    """
    Retourne (steps, comment) :
      - steps: liste [(Role, sla_days), ...]
      - comment: texte pour log/debug
    Si rien en BDD => None
    """
    category = GrievanceCategory.objects.filter(name__iexact=ticket.sub_category).first()
    if category and category.workflow and len(category.workflow) > 0:
        route = category.workflow["ROUTE"]
        sla = category.workflow["SLA"]
        steps = []
        for i in range(len(route)):
            role = Role.objects.filter(name__iexact=route[i]).first()
            steps.append((role, sla[i]))
        return steps, f"workflow={category.name}"
    else:
        cat = ticket.category
        grievance_type = GrievanceType.objects.get(name__iexact=cat)
        is_sensitive = grievance_type.is_sensitive
        wf = _load_workflow(is_sensitive, cat or None)
        if not wf:
            return None
        steps = [(s.role, s.sla_days) for s in wf.steps.all().order_by("order")]
        return steps, f"workflow={wf.name}"


def _fallback_chain(ticket):
    """Chaîne codée en dur (sécurité si pas de config)."""
    cat = ticket.category
    grievance_type = GrievanceType.objects.get(name__iexact=cat)
    if grievance_type.is_sensitive:
        names = ROUTE_SENSITIVE
    else:
        names = ROUTE_NON_SENSITIVE

    steps = []
    for name in names:
        role = Role.objects.filter(name=name).first()
        if role:
            steps.append((role, 0))
    return steps, "fallback"


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


def _most_specific_location_from_ticket(ticket):
    """
    Retourne la localité la plus précise disponible sur le ticket.
    """
    for fname in ("location",):
        if hasattr(ticket, fname):
            loc = getattr(ticket, fname, None)
            if loc:
                return loc
    return None


def _administrative_level_from_any_location(loc, type="D"):
    """Monte dans la hiérarchie pour trouver le niveau administratif parent."""
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


def _covers_location(user_role, target_loc: Location):
    """Vérifie si un utilisateur couvre la localité via LocationManager.allowed."""
    try:
        allowed_ids = Location.objects.get_allowed_ids(user_role)
        return target_loc.id in allowed_ids
    except Exception:
        return False


def _choose_assignee(role: Role, ticket):
    """
    Assigne un utilisateur du groupe en fonction de la localité du ticket.
    Stratégie :
      1) Municipalité exacte
      2) District exact
      3) Couverture étendue
      4) Fallback : premier user du rôle
    """
    base_qs = UserRole.objects.filter(role=role).select_related("user")
    target_loc = _most_specific_location_from_ticket(ticket)

    target_location = _administrative_level_from_any_location(target_loc, "W")
    if target_location:
        candidates = (
            base_qs.filter(user__usermunicipality__location=target_location)
            .order_by("-user__last_login", "id")
            .distinct()
        )
        if candidates.exists():
            return _as_django_user(candidates.first().user) if candidates.first() else None

    target_location = _administrative_level_from_any_location(target_loc, "D")
    if target_location:
        candidates = (
            base_qs.filter(user__userdistrict__location=target_location)
            .order_by("-user__last_login", "id")
            .distinct()
        )
        if candidates.exists():
            return _as_django_user(candidates.first().user) if candidates.first() else None

    if target_loc:
        for u in base_qs.order_by("-user__last_login", "id"):
            if _covers_location(u, target_loc):
                return _as_django_user(u.user) if u else None

    fallback = base_qs.order_by("id").first().user if base_qs.order_by("id").first() else None
    return _as_django_user(fallback)


def _get_assignment_recipient_emails(assignee=None):
    """
    Retourne uniquement l'email de l'assigné courant.
    L'envoi est volontairement limité au destinataire directement concerné.
    """
    if not assignee:
        return []

    email = _get_user_email_safe(assignee)
    return [email] if email else []


def _get_workflow_actor_recipient_emails(ticket, current_assignee=None):
    """
    Retourne les emails de tous les acteurs impliques dans le workflow :
      - assigne courant si present
      - tous les utilisateurs deja assignes dans l'historique
    Sans doublons.
    """
    emails = []
    seen_user_ids = set()

    if current_assignee:
        current_email = _get_user_email_safe(current_assignee)
        if current_email:
            emails.append(current_email)

        current_id = getattr(current_assignee, "id", None)
        if current_id is not None:
            seen_user_ids.add(str(current_id))

    json_ext = getattr(ticket, "json_ext", {}) or {}
    wf = json_ext.get("workflow", {}) or {}
    history = wf.get("history", []) or []

    history_user_ids = []
    for item in history:
        user_id = item.get("to_user_id")
        if user_id is None:
            continue

        user_id_str = str(user_id)
        if user_id_str in seen_user_ids:
            continue

        seen_user_ids.add(user_id_str)
        history_user_ids.append(user_id)

    if history_user_ids:
        users = User.objects.filter(id__in=history_user_ids)
        for user in users:
            email = _get_user_email_safe(user)
            if email and email not in emails:
                emails.append(email)

    return emails


def _build_escalation_email_context(ticket, target_group, assignee, sla_days, next_level, meta, triggered_by, event_type):
    json_ext = getattr(ticket, "json_ext", {}) or {}
    wf = json_ext.get("workflow", {}) or {}
    is_sensitive = _is_sensitive_ticket(ticket)
    description = getattr(ticket, "description", None) or getattr(ticket, "issue_description", None) or ""
    if is_sensitive:
        description = "Description masquee pour ce ticket sensible."

    return {
        "ticket_id": getattr(ticket, "id", None),
        "ticket_reference": getattr(ticket, "code", None) or getattr(ticket, "uuid", None) or str(getattr(ticket, "id", "")),
        "category": getattr(ticket, "category", None),
        "sub_category": getattr(ticket, "sub_category", None),
        "status": getattr(ticket, "status", None),
        "description": description,
        "location": str(getattr(ticket, "location", "") or ""),
        "target_role": getattr(target_group, "name", None) if target_group else None,
        "assignee_name": _get_user_full_name_safe(assignee),
        "assignee_email": _get_user_email_safe(assignee),
        "sla_days": sla_days,
        "due_date": getattr(ticket, "due_date", None),
        "escalation_level": next_level,
        "source": meta,
        "triggered_by": triggered_by,
        "event_type": event_type,
        "is_sensitive": is_sensitive,
        "workflow": wf,
    }


def _build_escalation_email_subject(context):
    ref = context.get("ticket_reference") or context.get("ticket_id")
    role = context.get("target_role") or "N/A"
    if context.get("event_type") == "assignment":
        return f"[GRM] Affectation plainte {ref} vers {role}"
    return f"[GRM] Escalade plainte {ref} vers {role}"


def _build_escalation_email_body(context):
    if context.get("event_type") == "assignment":
        intro = "La plainte suivante vous a ete affectee dans le systeme GRM."
        action_reason = "Vous recevez ce message car vous etes le nouvel assigne de cette plainte."
    else:
        intro = "La plainte suivante a ete escaladee dans le systeme GRM et vous a ete assignee."
        action_reason = "Vous recevez ce message car vous etes le nouvel assigne apres escalation."

    return f"""
Bonjour,

{intro}

Référence : {context.get('ticket_reference') or '-'}
Catégorie : {context.get('category') or '-'}
Sous-catégorie : {context.get('sub_category') or '-'}
Statut : {context.get('status') or '-'}
Niveau d'escalade : {context.get('escalation_level')}
Rôle cible : {context.get('target_role') or '-'}
Assigné actuel : {context.get('assignee_name') or '-'}
Email assigné : {context.get('assignee_email') or '-'}
Échéance : {context.get('due_date') or '-'}
Déclenché par : {context.get('triggered_by') or '-'}

Description :
{context.get('description') or '-'}

{action_reason}

Merci.
""".strip()


def _build_resolution_email_context(ticket, comment, triggered_by):
    is_sensitive = _is_sensitive_ticket(ticket)
    description = getattr(ticket, "description", None) or getattr(ticket, "issue_description", None) or ""
    resolution_comment = getattr(comment, "comment", None) or ""

    if is_sensitive:
        description = "Description masquee pour ce ticket sensible."
        resolution_comment = "Commentaire de resolution masque pour ce ticket sensible."

    return {
        "ticket_id": getattr(ticket, "id", None),
        "ticket_reference": getattr(ticket, "code", None) or str(getattr(ticket, "id", "")),
        "category": getattr(ticket, "category", None),
        "sub_category": getattr(ticket, "sub_category", None),
        "status": getattr(ticket, "status", None),
        "description": description,
        "resolution_comment": resolution_comment,
        "location": str(getattr(ticket, "location", "") or ""),
        "triggered_by": triggered_by,
        "is_sensitive": is_sensitive,
    }


def _build_resolution_email_subject(context):
    ref = context.get("ticket_reference") or context.get("ticket_id")
    return f"[GRM] Resolution plainte {ref}"


def _build_resolution_email_body(context):
    return f"""
Bonjour,

La plainte suivante a ete resolue dans le systeme GRM.

Reference : {context.get('ticket_reference') or '-'}
Categorie : {context.get('category') or '-'}
Sous-categorie : {context.get('sub_category') or '-'}
Statut : {context.get('status') or '-'}
Declenche par : {context.get('triggered_by') or '-'}

Description :
{context.get('description') or '-'}

Commentaire de resolution :
{context.get('resolution_comment') or '-'}

Vous recevez ce message car vous avez ete implique dans le traitement de cette plainte.

Merci.
""".strip()


def _send_escalation_notifications(
    ticket,
    target_group,
    assignee,
    sla_days,
    next_level,
    meta,
    triggered_by="Admin",
    event_type="escalation",
):
    """
    Envoie les notifications email APRES commit.
    Destinataire :
      - assigné courant uniquement
    """
    context = _build_escalation_email_context(
        ticket=ticket,
        target_group=target_group,
        assignee=assignee,
        sla_days=sla_days,
        next_level=next_level,
        meta=meta,
        triggered_by=triggered_by,
        event_type=event_type,
    )

    recipient_emails = _get_assignment_recipient_emails(assignee=assignee)

    if not recipient_emails:
        logger.warning(
            "Aucune adresse email trouvee pour la notification du ticket %s",
            getattr(ticket, "id", None),
        )
        return

    subject = _build_escalation_email_subject(context)
    body = _build_escalation_email_body(context)

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    if not from_email:
        logger.warning("DEFAULT_FROM_EMAIL non configuré, notification annulée.")
        return

    for recipient_email in recipient_emails:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=from_email,
            to=[recipient_email],
        )

        try:
            msg.send(fail_silently=False)
            logger.info(
                "Notification %s envoyee pour ticket=%s recipient=%s",
                event_type,
                getattr(ticket, "id", None),
                recipient_email,
            )
        except Exception:
            logger.exception(
                "Erreur lors de l'envoi de la notification %s pour ticket=%s recipient=%s",
                event_type,
                getattr(ticket, "id", None),
                recipient_email,
            )


def _send_resolution_notifications(ticket, comment, triggered_by="Admin", current_assignee=None):
    context = _build_resolution_email_context(
        ticket=ticket,
        comment=comment,
        triggered_by=triggered_by,
    )
    recipient_emails = _get_workflow_actor_recipient_emails(
        ticket=ticket,
        current_assignee=current_assignee if current_assignee is not None else getattr(ticket, "attending_staff", None),
    )

    if not recipient_emails:
        logger.warning(
            "Aucune adresse email trouvee pour la notification de resolution du ticket %s",
            getattr(ticket, "id", None),
        )
        return

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    if not from_email:
        logger.warning("DEFAULT_FROM_EMAIL non configuré, notification annulée.")
        return

    subject = _build_resolution_email_subject(context)
    body = _build_resolution_email_body(context)

    for recipient_email in recipient_emails:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=from_email,
            to=[recipient_email],
        )

        try:
            msg.send(fail_silently=False)
            logger.info(
                "Notification resolution envoyee pour ticket=%s recipient=%s",
                getattr(ticket, "id", None),
                recipient_email,
            )
        except Exception:
            logger.exception(
                "Erreur lors de l'envoi de la notification de resolution pour ticket=%s recipient=%s",
                getattr(ticket, "id", None),
                recipient_email,
            )


def reset_workflow_after_resolution(ticket: Ticket, username="Admin"):
    """
    Conserve l'historique existant et reaffecte le ticket
    au plus bas niveau du workflow.
    """
    cfg = _get_escalation_from_db(ticket)
    if not cfg:
        steps, meta = _fallback_chain(ticket)
    else:
        steps, meta = cfg

    if not steps:
        raise ValidationError("Aucune etape de workflow disponible pour reassigner le ticket")

    target_group, sla_days = steps[0]
    assignee = _choose_assignee(target_group, ticket)

    json_ext = getattr(ticket, "json_ext", {}) or {}
    wf = json_ext.get("workflow", {}) or {}
    now = timezone.now()
    history = wf.get("history", []) or []

    history.append({
        "at": now.isoformat(),
        "by": username,
        "to_role": target_group.name if target_group else None,
        "to_user_id": make_json_serializable(getattr(assignee, "id", None)),
        "to_user_fullname": _get_user_full_name_safe(assignee),
        "source": f"{meta}:resolution_reset",
        "sla_days": sla_days,
    })

    wf.update({
        "assignee_role": target_group.name if target_group else None,
        "escalation_level": 0,
        "last_escalated_at": now.isoformat(),
        "history": history,
    })

    json_ext["workflow"] = wf
    ticket.json_ext = make_json_serializable(json_ext)
    ticket.attending_staff = assignee
    if hasattr(ticket, "due_date"):
        ticket.due_date = timezone.now().date() + timezone.timedelta(days=sla_days) if sla_days and sla_days > 0 else None
    ticket.save(username=username)


def escalate_ticket(ticket: Ticket, username="Admin"):
    try:
        with transaction.atomic():
            target_group, sla_days, next_level, meta = _next_step(ticket)
            assignee = _choose_assignee(target_group, ticket)
            event_type = "assignment" if next_level == 0 else "escalation"

            if hasattr(ticket, "attending_staff"):
                ticket.attending_staff = assignee

            try:
                if next_level > 0 and ticket.status in [Ticket.TicketStatus.RECEIVED, Ticket.TicketStatus.OPEN]:
                    ticket.status = Ticket.TicketStatus.IN_PROGRESS
            except Exception:
                pass

            if hasattr(ticket, "due_date") and sla_days and sla_days > 0:
                ticket.due_date = timezone.now().date() + timezone.timedelta(days=sla_days)

            json_ext = getattr(ticket, "json_ext", {}) or {}
            wf = json_ext.get("workflow", {}) or {}
            now = timezone.now()
            history = wf.get("history", [])

            user_id = make_json_serializable(getattr(assignee, "id", None))
            assignee_full_name = _get_user_full_name_safe(assignee)

            history.append({
                "at": now.isoformat(),
                "by": username,
                "to_role": target_group.name if target_group else None,
                "to_user_id": user_id,
                "to_user_fullname": assignee_full_name,
                "source": meta,
                "sla_days": sla_days,
            })

            wf.update({
                "assignee_role": target_group.name if target_group else None,
                "escalation_level": next_level,
                "last_escalated_at": now.isoformat(),
                "history": history,
            })

            json_ext["workflow"] = wf
            json_ext = make_json_serializable(json_ext)

            try:
                json.dumps(json_ext)
            except Exception as e:
                raise ValidationError({"json_ext": f"Contenu non sérialisable: {e}"})

            ticket.json_ext = json_ext
            ticket.save(username=username)

            transaction.on_commit(
                lambda: _send_escalation_notifications(
                    ticket=ticket,
                    target_group=target_group,
                    assignee=assignee,
                    sla_days=sla_days,
                    next_level=next_level,
                    meta=meta,
                    triggered_by=username,
                    event_type=event_type,
                )
            )

            return ticket

    except Exception as exc:
        raise Exception(exc)
