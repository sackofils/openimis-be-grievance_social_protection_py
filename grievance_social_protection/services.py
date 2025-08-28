from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.db import transaction

from core.services import BaseService
from core.signals import register_service_signal
from core.services.utils import check_authentication as check_authentication, output_exception, \
    model_representation, output_result_success
from grievance_social_protection.models import Ticket, Comment, GrievanceType
from grievance_social_protection.validations import (
    TicketValidation,
    CommentValidation,
    validate_resolution
)
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from functools import lru_cache
from .models import EscalationWorkflow, EscalationStep

User = get_user_model()

class TicketService(BaseService):
    OBJECT_TYPE = Ticket

    def __init__(self, user, validation_class=TicketValidation):
        super().__init__(user, validation_class)

    @register_service_signal('ticket_service.create')
    def create(self, obj_data):
        self._get_content_type(obj_data)
        self._generate_code(obj_data)
        resolution_error = validate_resolution(obj_data)
        if resolution_error:
            raise ValidationError(resolution_error)
        return super().create(obj_data)

    @register_service_signal('ticket_service.update')
    def update(self, obj_data):
        self._get_content_type(obj_data)
        # resolution_error = validate_resolution(obj_data)
        #if resolution_error:
        #    raise ValidationError(resolution_error)
        return super().update(obj_data)

    @register_service_signal('ticket_service.delete')
    def delete(self, obj_data):
        return super().delete(obj_data)

    @register_service_signal('ticket_service.reopen_ticket')
    @check_authentication
    def reopen_ticket(self, obj_data):
        try:
            with transaction.atomic():
                self.validation_class.validate_update(self.user, **obj_data)
                ticket_id = obj_data.get('id')
                ticket = Ticket.objects.filter(id=ticket_id).first()
                ticket.status = Ticket.TicketStatus.OPEN
                self._check_if_comment_resolution(ticket_id)
                ticket.save(username=self.user.username)
                return {
                    "success": True,
                    "message": "Ok",
                    "detail": "reopen_ticket",
                }
        except Exception as exc:
            return output_exception(model_name=self.OBJECT_TYPE.__name__, method="reopen_ticket", exception=exc)

    @transaction.atomic
    def _check_if_comment_resolution(self, ticket_id):
        comment_queryset = Comment.objects.filter(ticket_id=ticket_id, is_resolution=True)
        if comment_queryset.exists():
            comment = comment_queryset.first()
            comment.is_resolution = False
            comment.save(username=self.user.username)

    def _get_content_type(self, obj_data):
        if 'reporter_type' in obj_data:
            content_type = ContentType.objects.get(model=obj_data['reporter_type'].lower())
            obj_data['reporter_type'] = content_type

    def _generate_code(self, obj_data):
        if not obj_data.get('code'):
            last_ticket_code = Ticket.objects.filter(code__startswith='GRS').aggregate(Max('code')).get('code__max')
            if last_ticket_code is None:
                last_ticket_code_numeric = 0
            else:
                last_ticket_code_numeric = int(last_ticket_code[3:])

            new_ticket_code = f'GRS{last_ticket_code_numeric + 1:08}'
            obj_data['code'] = new_ticket_code


    # ----- Helpers: chaîne d’escalade & choix de l’assigné -----
    @lru_cache(maxsize=128)
    def _load_workflow(self, is_sensitive: bool, category_slug: str | None) -> EscalationWorkflow | None:
        qs = EscalationWorkflow.objects.filter(active=True)
        # Priorité: correspondance exacte de catégorie si fournie
        if category_slug:
            wf = qs.filter(category_slug__iexact=category_slug).first()
            if wf:
                return wf
        # Sinon fallback sur is_sensitive (par défaut)
        return qs.filter(category_slug__isnull=True, is_sensitive=is_sensitive).first()

    def _get_escalation_from_db(self, ticket):
        """
        Retourne (steps, comment) :
          - steps: liste [(Group, sla_days), ...]
          - comment: texte pour log/debug
        Si rien en BDD => None
        """
        cat = ticket.category
        type = GrievanceType.objects.get(name__iexact=cat)
        is_sensitive = type.is_sensitive
        wf = self._load_workflow(is_sensitive, cat or None)
        if not wf:
            return None
        steps = [(s.group, s.sla_days) for s in wf.steps.all().order_by("order")]
        return steps, f"workflow={wf.name}"

    def _fallback_chain(self, ticket):
        """Chaîne codée en dur (sécurité si pas de config)."""
        cat = ticket.category
        type = GrievanceType.objects.get(name__iexact=cat)
        if type.is_sensitive:
            names = ["ETM", "DEVOPS"]
        else:
            names = ["CGR", "AC", "RAC", "ETM", "DEVOPS"]
        steps = []
        for name in names:
            grp = Group.objects.filter(name=name).first()
            if grp:
                steps.append((grp, 0))  # SLA=0 par défaut si fallback
        return steps, "fallback"

    def _next_step(self, ticket):
        """
        Détermine la prochaine étape d’escalade:
          - lit les steps BDD si dispo sinon fallback
          - lit json_ext.workflow.escalation_level pour connaître la position
        Retourne (group, sla_days, level, meta_label)
        """
        cfg = self._get_escalation_from_db(ticket)
        if not cfg:
            steps, meta = self._fallback_chain(ticket)
        else:
            steps, meta = cfg

        json_ext = getattr(ticket, "json_ext", {}) or {}
        wf = json_ext.get("workflow", {}) or {}
        level = wf.get("escalation_level", -1)
        next_index = level + 1
        if next_index >= len(steps):
            raise ValidationError("Maximum escalation level reached")
        group, sla_days = steps[next_index]
        return group, sla_days, next_index, meta

    def _choose_assignee(self, group: Group, ticket):
        """
        Choix simple: 1er user actif du groupe.
        Tu peux filtrer ici selon la région/préfecture du ticket si dispo sur User.
        """
        return User.objects.filter(groups=group).order_by("id").first()

    @register_service_signal("ticket_service.escalate_ticket")
    @check_authentication
    def escalate_ticket(self, obj_data):
        try:
            with transaction.atomic():
                ticket_id = obj_data.get("id") or obj_data.get("ticket_id")
                if not ticket_id:
                    raise ValidationError("Missing 'id' for escalation")

                ticket = Ticket.objects.get(id=ticket_id)

                # Détermine la prochaine étape
                target_group, sla_days, next_level, meta = self._next_step(ticket)
                assignee = self._choose_assignee(target_group, ticket)

                # Affectation & statut
                if hasattr(ticket, "attending_staff"):
                    ticket.attending_staff = assignee
                try:
                    if ticket.status in [Ticket.TicketStatus.RECEIVED, Ticket.TicketStatus.OPEN]:
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
                history.append({
                    "at": now.isoformat(),
                    "by": getattr(self.user, "username", None),
                    "to_role": target_group.name if target_group else None,
                    "to_user_id": getattr(assignee, "id", None),
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
                if hasattr(ticket, "json_ext"):
                    ticket.json_ext = json_ext

                ticket.save(username=self.user.username)
                return output_result_success(dict_representation=model_representation(ticket))

        except Exception as exc:
            return output_exception(model_name=self.OBJECT_TYPE.__name__, method="escalate_ticket", exception=exc)



class CommentService:
    OBJECT_TYPE = Comment

    def __init__(self, user, validation_class=CommentValidation):
        self.user = user
        self.validation_class = validation_class

    @register_service_signal('comment_service.create')
    @check_authentication
    def create(self, obj_data):
        try:
            with transaction.atomic():
                self._get_content_type(obj_data)
                ticket_id = obj_data.get('ticket_id')
                self.validation_class.validate_create(self.user, **obj_data)

                comment_obj = self.OBJECT_TYPE(**obj_data)
                response_data = self.save_instance(comment_obj)
                self._update_ticket_comment_ids(ticket_id, response_data['data']['id'])

                return response_data

        except Exception as exc:
            return output_exception(
                model_name=self.OBJECT_TYPE.__name__,
                method="create",
                exception=exc
            )

    @transaction.atomic
    def _update_ticket_comment_ids(self, ticket_id, comment_id):
        ticket = Ticket.objects.filter(id=ticket_id).first()
        if ticket:
            json_ext = ticket.json_ext or {}
            comment_ids = json_ext.get('comment_ids', [])
            comment_ids.append(comment_id)
            json_ext['comment_ids'] = comment_ids
            ticket.json_ext = json_ext
            ticket.save(username=self.user.username)

    @register_service_signal('comment_service.resolve_grievance_by_comment')
    @check_authentication
    def resolve_grievance_by_comment(self, obj_data):
        try:
            with transaction.atomic():
                self.validation_class.validate_resolve_grievance_by_comment(self.user, **obj_data)
                comment = Comment.objects.filter(id=obj_data.get('id')).first()
                ticket = comment.ticket
                ticket.status = Ticket.TicketStatus.CLOSED
                comment.is_resolution = True
                ticket.save(username=self.user.username)
                comment.save(username=self.user.username)
                return {
                    "success": True,
                    "message": "Ok",
                    "detail": "resolve_grievance_by_comment",
                }
        except Exception as exc:
            return output_exception(model_name=self.OBJECT_TYPE.__name__, method="resolve_grievance_by_comment", exception=exc)

    def save_instance(self, obj_):
        obj_.save(username=self.user.username)
        dict_repr = model_representation(obj_)
        return output_result_success(dict_representation=dict_repr)

    def _get_content_type(self, obj_data):
        if 'commenter_type' in obj_data:
            content_type = ContentType.objects.get(model=obj_data['commenter_type'].lower())
            obj_data['commenter_type'] = content_type
