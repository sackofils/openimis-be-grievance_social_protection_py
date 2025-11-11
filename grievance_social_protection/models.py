from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from graphql import ResolveInfo

import core
from core import models as core_models
from core.models import HistoryBusinessModel, User, HistoryModel, Role
from location.models import Location
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from django.urls import reverse



def check_if_user_or_individual(generic_field):
    individual = apps.get_model('individual', 'Individual')
    beneficiary = apps.get_model('social_protection', 'Beneficiary')
    if not isinstance(generic_field, (User, individual, beneficiary)):
        raise ValueError('Reporter must be either a User or a Beneficiary or an Individual.')

class EscalationWorkflow(models.Model):
    """
    Un workflow identifié (ex: 'default-sensitive', 'default-non-sensitive').
    On peut le sélectionner par sensibilité ou par slug de catégorie si nécessaire.
    """
    name = models.CharField(max_length=128, unique=True)
    is_sensitive = models.BooleanField(default=False)  # True => plaintes sensibles
    category_slug = models.CharField(
        max_length=128, blank=True, null=True,
        help_text="Optionnel: pour cibler une catégorie précise (slug)."
    )
    active = models.BooleanField(default=True)

    def __str__(self):
        tag = "sensible" if self.is_sensitive else "non sensible"
        return f"{self.name} ({tag})"


class EscalationStep(models.Model):
    """
    Une étape d’escalade: rôle cible (groupe) + SLA (jours).
    L’ordre détermine la progression de l’escalade.
    """
    workflow = models.ForeignKey(EscalationWorkflow, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveIntegerField(help_text="Ordre d’escalade (0, 1, 2, ...)")
    role = models.ForeignKey(Role, null=True, on_delete=models.PROTECT, help_text="Groupe/role assigné (ex: CGR, AC, RAC, ETM, DEVOPS)")
    sla_days = models.PositiveIntegerField(default=0, help_text="Délai (jours) pour cette étape")

    class Meta:
        unique_together = (("workflow", "order"),)
        ordering = ("workflow", "order")

    def __str__(self):
        return f"{self.workflow.name} [{self.order}] -> {self.role.name} ({self.sla_days} j)"

class TicketDeathDossier(models.Model):
    ticket = models.OneToOneField("Ticket", on_delete=models.CASCADE, related_name="death_dossier")

    # 4 cases à cocher
    certificat_deces = models.BooleanField(default=False)
    pv_remplacant = models.BooleanField(default=False)
    id_nouveau_beneficiaire = models.BooleanField(default=False)
    fiche_engagement = models.BooleanField(default=False)

    # 4 fichiers (optionnel)
    file_certificat_deces = models.FileField(upload_to="death_dossiers/", null=True, blank=True)
    file_pv_remplacant = models.FileField(upload_to="death_dossiers/", null=True, blank=True)
    file_id_nouveau_beneficiaire = models.FileField(upload_to="death_dossiers/", null=True, blank=True)
    file_fiche_engagement = models.FileField(upload_to="death_dossiers/", null=True, blank=True)

    # Informations sur le nouveau bénéficiaire
    code_beneficiaire = models.CharField(max_length=32, null=True, blank=True)
    nom_beneficiaire = models.CharField(max_length=255, null=True, blank=True)
    prenom_beneficiaire = models.CharField(max_length=255, null=True, blank=True)
    sexe_beneficiaire = models.CharField(max_length=10, null=True, blank=True, choices=[("M", "Masculin"), ("F", "Féminin")])

    complete = models.BooleanField(default=False)

    def update_completeness(self):
        self.complete = all([
            self.certificat_deces,
            self.pv_remplacant,
            self.id_nouveau_beneficiaire,
            self.fiche_engagement,
        ])
        self.save(update_fields=["complete"])

    def __str__(self):
        return f"DeathDossier(ticket={self.ticket_id})"


class Ticket(HistoryBusinessModel):
    class TicketStatus(models.TextChoices):
        # TMP FOR NOW
        RECEIVED = 'RECEIVED', 'Received'
        OPEN = 'OPEN', 'Open'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        RESOLVED = 'RESOLVED', 'Resolved'
        CLOSED = 'CLOSED', 'Closed'

    key = models.TextField(null=True, blank=True)
    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(max_length=255, blank=True, null=True)
    code = models.CharField(max_length=16, unique=True, blank=True, null=True)  # ← mappe id_plainte_generer

    reporter_type = models.ForeignKey(ContentType, on_delete=models.DO_NOTHING, null=True, blank=True)
    reporter_id = models.CharField(max_length=255, null=True, blank=True)
    reporter = GenericForeignKey('reporter_type', 'reporter_id')

    attending_staff = models.ForeignKey(User, models.DO_NOTHING, blank=True, null=True)
    date_of_incident = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=20, blank=False, null=False, choices=TicketStatus.choices, default=TicketStatus.RECEIVED
    )
    priority = models.CharField(max_length=20, blank=True, null=True)
    due_date = models.DateField(blank=True, null=True)

    category = models.CharField(max_length=255, blank=True, null=True)
    sub_category = models.CharField(max_length=255, blank=True, null=True)
    sub_category_level1 = models.CharField(max_length=255, blank=True, null=True)
    flags = models.CharField(max_length=255, blank=True, null=True)
    channel = models.CharField(max_length=255, blank=True, null=True)
    resolution = models.CharField(max_length=255, blank=True, null=True)

    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.DO_NOTHING,
                               related_name="tickets_location")

    # ménage / référence bénéficiaire
    household_code = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    # infos déclarant (si pas résolues via GenericFK)
    reporter_name = models.CharField(max_length=128, blank=True, null=True)
    reporter_phone = models.CharField(max_length=64, blank=True, null=True)

    is_exported = models.BooleanField(default=False)

    escalation_level = models.PositiveSmallIntegerField(default=0, db_index=True)
    max_escalation_level = models.PositiveSmallIntegerField(default=3)  # plafond d’escalade
    last_escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_log = models.JSONField(default=list, blank=True)  # trace des étapes

    # stockage des champs annexes/“non mappés”
    json_ext = models.JSONField(default=dict, blank=True)

    def clean(self):
        super().clean()
        if self.reporter:
            check_if_user_or_individual(self.reporter)

    def __str__(self):
        return f"{self.title or self.code or self.pk}"

    @classmethod
    def filter_queryset(cls, queryset=None):
        if queryset is None:
            queryset = cls.objects.all()
        queryset = queryset.filter(*core.filter_validity())
        return queryset

    @classmethod
    def get_queryset(cls, queryset, user):
        queryset = cls.filter_queryset(queryset)
        if isinstance(user, ResolveInfo):
            user = user.context.user
        if settings.ROW_SECURITY and user.is_anonymous:
            return queryset.filter(id=None)
        if settings.ROW_SECURITY:
            pass
        return queryset


class TicketMutation(core_models.UUIDModel, core_models.ObjectMutation):
    ticket = models.ForeignKey(Ticket, models.DO_NOTHING,
                               related_name='mutations')
    mutation = models.ForeignKey(
        core_models.MutationLog, models.DO_NOTHING, related_name='tickets')

    class Meta:
        managed = True
        db_table = "ticket_TicketMutation"


class Comment(HistoryModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.DO_NOTHING, null=False, blank=False)
    commenter_type = models.ForeignKey(ContentType, on_delete=models.DO_NOTHING, null=True, blank=True)
    commenter_id = models.CharField(max_length=255, null=True, blank=True)
    commenter = GenericForeignKey('commenter_type', 'commenter_id')
    comment = models.TextField(blank=False, null=False)
    is_resolution = models.BooleanField(blank=False, null=False, default=False)

    def clean(self):
        super().clean()
        if self.commenter:
            check_if_user_or_individual(self.commenter)

        if self.is_resolution:
            existing_resolved_comments = Comment.objects.filter(ticket=self.ticket, is_resolution=True)

            if self.id:
                existing_resolved_comments = existing_resolved_comments.exclude(id=self.id)

            if existing_resolved_comments.exists():
                raise ValueError("Another comment for this ticket is already marked as resolved.")

class GrievanceChannel(models.Model):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "name")

    def __str__(self):
        return f"{self.code} — {self.name}"

class GrievanceFlag(models.Model):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "name")

    def __str__(self):
        return f"{self.code} — {self.name}"

class GrievanceType(models.Model):
    """
    Niveau 1 (ex: cas_sensible, cas_non_sensible, cas_speciaux)
    """
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    is_sensitive = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "name")

    def __str__(self):
        return f"{self.code} — {self.name}"


class GrievanceCategory(models.Model):
    """
    Niveau 2 (ex: pour cas_non_sensible → téléphone, paiement, etc.)
    """
    parent = models.ForeignKey(
        GrievanceType, on_delete=models.CASCADE, related_name="grievance_types"
    )
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    workflow = models.JSONField(default=dict, null=True)
    sla = models.PositiveIntegerField(default=21)

    class Meta:
        unique_together = (("parent", "code"),)
        ordering = ("parent__order", "order", "name")

    def __str__(self):
        return f"{self.parent.code}/{self.code} — {self.name}"


class GrievanceSubCategory(models.Model):
    """
    Niveau 3 (ex: pour téléphone → perdu, défectueux, etc.)
    """
    parent = models.ForeignKey(
        GrievanceCategory, on_delete=models.CASCADE, related_name="grievance_sub_categories"
    )
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = (("parent", "code"),)
        ordering = ("parent__parent__order", "parent__order", "order", "name")

    def __str__(self):
        return f"{self.parent.parent.code}/{self.parent.code}/{self.code} — {self.name}"


# LEFT IF NEEDED IN THE FUTURE

# class TicketAttachment(core_models.UUIDModel, core_models.UUIDVersionedModel, ):
#     uuid = models.CharField(db_column='AttachmentUUID', max_length=36, default=uuid.uuid4, unique=True)
#     ticket = models.ForeignKey(
#         Ticket, models.DO_NOTHING, related_name='attachment', db_column='TicketId', blank=True, null=True)
#     filename = models.TextField(max_length=1000, blank=True, null=True)
#     mime_type = models.TextField(max_length=255, blank=True, null=True)
#     url = models.TextField(max_length=1000, blank=True, null=True)
#     document = models.TextField(blank=True, null=True)
#     date = fields.DateField(blank=True, default=py_datetime.now)
#
#     def __str__(self):
#         return f"{self.filename}"
#
#     def full_file_path(self):
#         if not TicketConfig.tickets_attachments_root_path or not self.filename:
#             return None
#         return os.path.join(TicketConfig.tickets_attachments_root_path, self.filename)
#
#     class Meta:
#         managed = True
#         db_table = 'tblTicketAttachment'
#
#     @classmethod
#     def filter_queryset(cls, queryset=None):
#         if queryset is None:
#             queryset = cls.objects.all()
#         queryset = queryset.filter(*core.filter_validity())
#         return queryset
#
#     @classmethod
#     def get_queryset(cls, queryset, user):
#         queryset = cls.filter_queryset(queryset)
#         if isinstance(user, ResolveInfo):
#             user = user.context.user
#         if settings.ROW_SECURITY and user.is_anonymous:
#             return queryset.filter(id=None)
#         if settings.ROW_SECURITY:
#             pass
#         return queryset


# LEFT IF NEEDED IN THE FUTURE

# class AttachmentMutation(core_models.UUIDModel, core_models.ObjectMutation):
#     ticket = models.ForeignKey(TicketAttachment, models.DO_NOTHING,
#                                related_name='mutations')
#     mutation = models.ForeignKey(
#         core_models.MutationLog, models.DO_NOTHING, related_name='attachment')
#
#     class Meta:
#         managed = True
#         db_table = "ticket_AttachmentMutation"
