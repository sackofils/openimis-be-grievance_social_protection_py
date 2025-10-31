import uuid
import graphene
from graphene_file_upload.scalars import Upload
from core.gql.gql_mutations.base_mutation import BaseHistoryModelCreateMutationMixin, BaseMutation, \
    BaseHistoryModelUpdateMutationMixin, BaseHistoryModelDeleteMutationMixin
from core.schema import OpenIMISMutation
from .models import Ticket, TicketMutation, Comment, TicketDeathDossier

from django.core.exceptions import ValidationError, PermissionDenied
from .apps import TicketConfig
from django.utils.translation import gettext_lazy as _

from .services import TicketService, CommentService
from .validations import user_associated_with_ticket
from django.db import transaction
from core.services.utils import  output_exception, model_representation, output_result_success

class CreateTicketInputType(OpenIMISMutation.Input):
    class TicketStatusEnum(graphene.Enum):
        RECEIVED = Ticket.TicketStatus.RECEIVED
        OPEN = Ticket.TicketStatus.OPEN
        IN_PROGRESS = Ticket.TicketStatus.IN_PROGRESS
        RESOLVED = Ticket.TicketStatus.RESOLVED
        CLOSED = Ticket.TicketStatus.CLOSED

    key = graphene.String(required=False)
    title = graphene.String(required=False)
    description = graphene.String(required=False)
    reporter_type = graphene.String(required=False, max_lenght=255)
    reporter_id = graphene.String(required=False, max_lenght=255)
    attending_staff_id = graphene.UUID(required=False)
    location_id = graphene.Int(required=True)
    date_of_incident = graphene.Date(required=False)
    status = graphene.Field(TicketStatusEnum, required=False)
    priority = graphene.String(required=False)
    due_date = graphene.Date(required=False)
    category = graphene.String(required=True)
    sub_category = graphene.String(required=False)
    sub_category_level1 = graphene.String(required=False)
    flags = graphene.String(required=False)
    channel = graphene.String(required=False)
    resolution = graphene.String(required=False)
    # json_ext = GenericScalar(required=False)


class UpdateTicketInputType(CreateTicketInputType):
    id = graphene.UUID(required=True)


class ResolveGrievanceByCommentInputType(OpenIMISMutation.Input):
    id = graphene.UUID(required=True)

class EscalateTicketInputType(OpenIMISMutation.Input):
    id = graphene.UUID(required=True)

class CreateCommentInputType(OpenIMISMutation.Input):
    ticket_id = graphene.UUID(required=True)
    commenter_type = graphene.String(required=False, max_lenght=255)
    commenter_id = graphene.String(required=False, max_lenght=255)
    comment = graphene.String(required=True)


class CreateTicketMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateTicketMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(TicketConfig.gql_mutation_create_tickets_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TicketService(user)
        response = service.create(data)
        if client_mutation_id:
            ticket_id = response['data']['id']
            ticket = Ticket.objects.get(id=ticket_id)
            TicketMutation.object_mutated(user, client_mutation_id=client_mutation_id, ticket=ticket)

        if not response['success']:
            return response
        return None

    class Input(CreateTicketInputType):
        pass


class UpdateTicketMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "UpdateTicketMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(TicketConfig.gql_mutation_update_tickets_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TicketService(user)
        response = service.update(data)
        if client_mutation_id:
            ticket_id = response['data']['id']
            ticket = Ticket.objects.get(id=ticket_id)
            TicketMutation.object_mutated(user, client_mutation_id=client_mutation_id, ticket=ticket)
        if not response['success']:
            return response
        return None

    class Input(UpdateTicketInputType):
        pass


class DeleteTicketMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeleteTicketMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                TicketConfig.gql_mutation_delete_tickets_perms):
            raise ValidationError("mutation.authentication_required")

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class CreateCommentMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateCommentMutation"
    _mutation_module = "grievance_social_protection"
    _model = Comment

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if user.has_perms(TicketConfig.gql_mutation_delete_tickets_perms):
            return
        if user_associated_with_ticket(user):
            return
        raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        if "commenter_type" in data:
            data['commenter_type'] = data.get('commenter_type', '').lower()
        service = CommentService(user)
        response = service.create(data)

        if not response['success']:
            return response
        return None

    class Input(CreateCommentInputType):
        pass


class ResolveGrievanceByCommentMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "ResolveGrievanceByCommentMutation"
    _mutation_module = "grievance_social_protection"
    _model = Comment

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(TicketConfig.gql_mutation_resolve_grievance_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = CommentService(user)
        response = service.resolve_grievance_by_comment(data)
        if client_mutation_id:
            comment_id = data.get('id')
            ticket = Comment.objects.get(id=comment_id).ticket
            TicketMutation.object_mutated(user, client_mutation_id=client_mutation_id, ticket=ticket)

        if not response['success']:
            return response
        return None

    class Input(ResolveGrievanceByCommentInputType):
        pass


class ReopenTicketMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "ReopenTicketMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(TicketConfig.gql_mutation_update_tickets_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TicketService(user)
        response = service.reopen_ticket(data)
        if client_mutation_id:
            ticket_id = data.get('id')
            ticket = Ticket.objects.get(id=ticket_id)
            TicketMutation.object_mutated(user, client_mutation_id=client_mutation_id, ticket=ticket)

        if not response['success']:
            return response
        return None

    class Input(ResolveGrievanceByCommentInputType):
        pass

class EscalateTicketMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "EscalateTicketMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        # même permission que pour update (à adapter si tu veux une perm spécifique)
        if not user.has_perms(TicketConfig.gql_mutation_update_tickets_perms):
            raise ValidationError(_("mutation.authentication_required"))

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id', None)
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TicketService(user)
        response = service.escalate_ticket(data)

        # Journalisation (bus d’événements)
        if client_mutation_id:
            try:
                ticket_id = data.get('id') or (response.get('data') or {}).get('id')
                if ticket_id:
                    ticket = Ticket.objects.get(id=ticket_id)
                    TicketMutation.object_mutated(user, client_mutation_id=client_mutation_id, ticket=ticket)
            except Exception:
                pass

        if not response.get('success'):
            return response
        return None

    class Input(EscalateTicketInputType):
        pass

class ExportSelectedTicketsInput(OpenIMISMutation.Input):
    ticket_ids = graphene.List(graphene.String, required=True)

class ExportSelectedTicketsMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "ExportSelectedTicketsMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket
    class Input(ExportSelectedTicketsInput):
        pass

    success = graphene.Boolean()
    files = graphene.List(graphene.String)
    message = graphene.String()

    @classmethod
    def mutate(cls, root, info, input):
        user = info.context.user
        if not user.has_perms(TicketConfig.gql_query_tickets_perms):
            raise PermissionDenied(_("unauthorized"))

        raw_ids = input.get("ticket_ids") or []
        if not raw_ids:
            raise ValidationError(_("No tickets provided"))

        # Conversion des UUID strings → UUID valides
        valid_ids = []
        for rid in raw_ids:
            try:
                valid_ids.append(uuid.UUID(str(rid)))
            except Exception:
                continue
        if not valid_ids:
            raise ValidationError(_("No valid ticket UUIDs provided"))

        # Imports différés (évite les dépendances circulaires)
        from .exports import (
            export_selected_tickets_xlsx,
            export_plainte_code_errone_xlsx,
            export_plainte_reactivation_sim_xlsx,
        )

        # --- Récupère les tickets (resolution, sub_category, sub_category_level1) ---
        tickets = list(
            Ticket.objects.filter(id__in=valid_ids).values(
                "id",
                "resolution",
                "sub_category",
                "sub_category_level1",
            )
        )

        if not tickets:
            raise ValidationError(_("No matching tickets found"))

        # --- Regroupement dynamique ---
        grouped = {
            "reactivation": [],
            "code_errone": [],
            "autre": [],
        }

        for t in tickets:
            resolution = (t.get("resolution") or "").lower()
            sub = (t.get("sub_category_level1") or t.get("sub_category") or "").lower()

            # Mots-clés logiques de regroupement
            if "réactivation" in resolution or "reactivation" in resolution:
                grouped["reactivation"].append(t["id"])
            elif "réinitialisation" in resolution or "code erroné" in resolution or "code errone" in resolution:
                grouped["code_errone"].append(t["id"])
            elif "décès" in sub or "deces" in sub:
                grouped["autre"].append(t["id"])

        # --- Génère les exports selon les groupes ---
        file_urls = []
        if grouped["reactivation"]:
            file_urls.append(export_plainte_reactivation_sim_xlsx(grouped["reactivation"]))
        if grouped["code_errone"]:
            file_urls.append(export_plainte_code_errone_xlsx(grouped["code_errone"]))
        if grouped["autre"]:
            file_urls.append(export_selected_tickets_xlsx(grouped["autre"]))

        message = _("Exports generated successfully")
        return ExportSelectedTicketsMutation(
            success=True,
            files=file_urls,
            message=message,
        )

class EscalateTicketsMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    """
    Mutation GraphQL pour escalader plusieurs tickets à la fois
    (structure conforme au modèle OpenIMIS)
    """

    _mutation_class = "EscalateTicketsMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket

    class Input(OpenIMISMutation.Input):
        ticket_ids = graphene.List(graphene.String, required=True)
        message = graphene.String(required=True)

    #success = graphene.Boolean()
    #count = graphene.Int()
    #message = graphene.String()

    @classmethod
    def _validate_mutation(cls, user, **data):
        if not user.has_perms(TicketConfig.gql_mutation_update_tickets_perms):
            raise ValidationError(_("mutation.authentication_required"))

        ids = data.get("ticket_ids") or []
        if not ids:
            raise ValidationError(_("No tickets provided"))

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop("client_mutation_id", None)
        data.pop("client_mutation_label", None)

        ticket_ids = data.get("ticket_ids") or []
        message = data.get("message") or ""

        service = TicketService(user)
        comment_service = CommentService(user)

        # Rôles utilisateur
        roles = list(user.user_roles.values_list("role__name", flat=True)) if hasattr(user, "user_roles") else []
        user_roles_upper = [r.upper() for r in roles]

        # Rôles à accès complet
        full_access_roles = {"CNGR", "DEVOPS", "SAUVEGARDES"}

        is_super_admin = user.is_superuser or (set(user_roles_upper) & full_access_roles)

        done = 0

        try:
            with transaction.atomic():
                for t in Ticket.objects.filter(id__in=ticket_ids):
                    # ---------------------------
                    # Vérification de niveau / rôle
                    # ---------------------------
                    ticket_role = (t.json_ext or {}).get("workflow", {}).get("assignee_role", "")
                    ticket_role_upper = ticket_role.upper() if ticket_role else ""

                    # Si l'utilisateur n'a pas d'accès complet
                    if not is_super_admin:
                        # On vérifie si le rôle du ticket correspond à l’un des rôles de l’utilisateur
                        if ticket_role_upper and ticket_role_upper not in user_roles_upper:
                            raise PermissionDenied(
                                _("Vous ne pouvez pas escalader un ticket appartenant à un autre rôle ou niveau.")
                            )

                    # ---------------------------
                    # Escalade et commentaire
                    # ---------------------------
                    comment_service.create({
                        "ticket_id": t.id,
                        "comment": message,
                        "commenter": user,
                        "commenter_id": user.id
                    })

                    resp = service.escalate_ticket({"id": t.id})
                    if isinstance(resp, dict) and resp.get("success"):
                        done += 1

            # Journalisation globale (mutation log)
            if client_mutation_id:
                TicketMutation.object_mutated(user, client_mutation_id=client_mutation_id, ticket=None)

        except Exception as e:
            return str(e)
        return None


class ResolveTicketsMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    """
    Mutation GraphQL pour marquer plusieurs tickets comme résolus (bulk)
    conforme au modèle BaseMutation / OpenIMIS.
    """

    _mutation_class = "ResolveTicketsMutation"
    _mutation_module = "grievance_social_protection"
    _model = Ticket

    class Input(OpenIMISMutation.Input):
        ticket_ids = graphene.List(graphene.String, required=True)
        message = graphene.String(required=True)

    #success = graphene.Boolean()
    #count = graphene.Int()
    #message = graphene.String()

    @classmethod
    def _validate_mutation(cls, user, **data):
        if not user.has_perms(TicketConfig.gql_mutation_update_tickets_perms):
            raise ValidationError(_("mutation.authentication_required"))

        ids = data.get("ticket_ids") or []
        if not ids:
            raise ValidationError(_("No tickets provided"))

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop("client_mutation_id", None)
        data.pop("client_mutation_label", None)

        ticket_ids = data.get("ticket_ids") or []
        message = data.get("message") or ""

        ticket_service = TicketService(user)
        comment_service = CommentService(user)

        # Récupère les rôles de l’utilisateur
        roles = list(user.user_roles.values_list("role__name", flat=True)) if hasattr(user, "user_roles") else []
        user_roles_upper = [r.upper() for r in roles]
        full_access_roles = {"CNGR", "DEVOPS", "SAUVEGARDES"}

        is_super_admin = user.is_superuser or (set(user_roles_upper) & full_access_roles)

        done = 0

        try:
            with transaction.atomic():
                for t in Ticket.objects.filter(id__in=ticket_ids):
                    # Récupère le rôle assigné du ticket
                    ticket_role = (t.json_ext or {}).get("workflow", {}).get("assignee_role", "")
                    ticket_role_upper = ticket_role.upper() if ticket_role else ""

                    # Si l’utilisateur n’a pas d’accès complet, vérifier son rôle
                    if not is_super_admin:
                        if ticket_role_upper and ticket_role_upper not in user_roles_upper:
                            raise PermissionDenied(
                                _("Vous ne pouvez pas résoudre un ticket appartenant à un autre rôle ou niveau.")
                            )

                    #  Créer un commentaire
                    c_resp = comment_service.create({
                        "ticket_id": t.id,
                        "comment": message,
                        "commenter": user,
                        "commenter_id": user.id
                    })

                    # Extraire le comment.id de la réponse
                    comment_id = None
                    try:
                        comment_id = (c_resp or {}).get("data", {}).get("id")
                    except Exception:
                        comment_id = None

                    # Marquer comme résolu via le service
                    if comment_id:
                        r = comment_service.resolve_grievance_by_comment({"id": comment_id})
                        if isinstance(r, dict) and r.get("success"):
                            done += 1

            # Journalisation de la mutation globale
            if client_mutation_id:
                TicketMutation.object_mutated(user, client_mutation_id=client_mutation_id, ticket=None)

        except Exception as exc:
            response = output_exception(model_name=cls._model.__name__, method="ResolveTicketsMutation", exception=exc)
            return response
        return None

# ======================================================
# === MUTATION UPLOAD DOSSIER DECES ====================
# ======================================================

class UpdateTicketDeathDossierMutation(graphene.Mutation):
    class Arguments:
        ticket_id = graphene.ID(required=True)

        # Cases à cocher
        certificat_deces = graphene.Boolean(required=False)
        pv_remplacant = graphene.Boolean(required=False)
        id_nouveau_beneficiaire = graphene.Boolean(required=False)
        fiche_engagement = graphene.Boolean(required=False)

        # Fichiers
        file_certificat_deces = Upload(required=False)
        file_pv_remplacant = Upload(required=False)
        file_id_nouveau_beneficiaire = Upload(required=False)
        file_fiche_engagement = Upload(required=False)

        # Nouveau bénéficiaire
        code_beneficiaire = graphene.String(required=False)
        nom_beneficiaire = graphene.String(required=False)
        prenom_beneficiaire = graphene.String(required=False)
        sexe_beneficiaire = graphene.String(required=False)

    ok = graphene.Boolean()
    complete = graphene.Boolean()
    dossier = graphene.Field("grievance_social_protection.gql_queries.TicketDeathDossierGQLType")

    @classmethod
    @transaction.atomic
    def mutate(
        cls,
        root,
        info,
        ticket_id,
        **kwargs,
    ):
        try:
            ticket = Ticket.objects.get(id=ticket_id)
        except Ticket.DoesNotExist:
            raise ValidationError(f"Ticket {ticket_id} introuvable")

        dossier, _ = TicketDeathDossier.objects.get_or_create(ticket=ticket)

        # Champs booléens
        bool_fields = [
            "certificat_deces",
            "pv_remplacant",
            "id_nouveau_beneficiaire",
            "fiche_engagement",
        ]
        for f in bool_fields:
            if f in kwargs:
                setattr(dossier, f, kwargs[f])

        # Fichiers uploadés
        file_fields = {
            "file_certificat_deces": "file_certificat_deces",
            "file_pv_remplacant": "file_pv_remplacant",
            "file_id_nouveau_beneficiaire": "file_id_nouveau_beneficiaire",
            "file_fiche_engagement": "file_fiche_engagement",
        }
        for gql_field, model_field in file_fields.items():
            file_obj = kwargs.get(gql_field)
            if file_obj:
                setattr(dossier, model_field, file_obj)

        # Informations bénéficiaire
        for f in ["nom_beneficiaire", "prenom_beneficiaire", "sexe_beneficiaire", "code_beneficiaire"]:
            if f in kwargs:
                setattr(dossier, f, kwargs[f])

        dossier.save()

        # Vérifie si complet
        complete = (
            dossier.certificat_deces
            and dossier.pv_remplacant
            and dossier.id_nouveau_beneficiaire
            and dossier.fiche_engagement
        )
        dossier.complete = complete
        dossier.save()

        return UpdateTicketDeathDossierMutation(
            ok=True,
            complete=complete,
            dossier=dossier,
        )
