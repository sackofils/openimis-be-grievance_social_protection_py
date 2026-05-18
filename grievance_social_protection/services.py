import json
import uuid
import datetime
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Max, Q
from django.db import transaction

from core.services import BaseService
from core.signals import register_service_signal
from core.models.user import Role, UserRole, InteractiveUser, User
from core.services.utils import check_authentication as check_authentication, output_exception, \
    model_representation, output_result_success
from grievance_social_protection.models import Ticket, Comment, GrievanceType
from grievance_social_protection.validations import (
    TicketValidation,
    CommentValidation,
    validate_resolution
)
from grievance_social_protection.escalation_helper import (
    escalate_ticket,
    reset_workflow_after_resolution,
    _send_resolution_notifications,
)
from django.contrib.auth import get_user_model


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

    @register_service_signal("ticket_service.escalate_ticket")
    @check_authentication
    def escalate_ticket(self, obj_data):
        try:
            with transaction.atomic():
                ticket_id = obj_data.get("id") or obj_data.get("ticket_id")
                if not ticket_id:
                    raise ValidationError("Missing 'id' for escalation")

                ticket = Ticket.objects.get(id=ticket_id)
                ticket = escalate_ticket(ticket, username=self.user.username)
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
                previous_assignee = ticket.attending_staff
                ticket.status = Ticket.TicketStatus.CLOSED
                comment.is_resolution = True
                reset_workflow_after_resolution(ticket, username=self.user.username)
                comment.save(username=self.user.username)
                transaction.on_commit(
                    lambda: _send_resolution_notifications(
                        ticket=ticket,
                        comment=comment,
                        triggered_by=self.user.username,
                        current_assignee=previous_assignee,
                    )
                )
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
