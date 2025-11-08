import graphene
from django.contrib.auth.models import AnonymousUser

from core.schema import OrderedDjangoFilterConnectionField
from core.schema import signal_mutation_module_validate
from django.db.models import Q
import graphene_django_optimizer as gql_optimizer

from core.utils import append_validity_filter
from .apps import MODULE_NAME

from .gql_queries import *
from .gql_mutations import *
from django.utils.translation import gettext_lazy as _
from django.db.models.expressions import RawSQL
from django.core.exceptions import PermissionDenied


class Query(graphene.ObjectType):
    tickets = OrderedDjangoFilterConnectionField(
        TicketGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        show_history=graphene.Boolean(),
        client_mutation_id=graphene.String(),
        ticket_version=graphene.Int(),
    )

    ticketsStr = OrderedDjangoFilterConnectionField(
        TicketGQLType,
        str=graphene.String(),
    )
    # ticket_attachments = DjangoFilterConnectionField(TicketAttachmentGQLType)

    ticket_details = OrderedDjangoFilterConnectionField(
        TicketGQLType,
        # showHistory=graphene.Boolean(),
        orderBy=graphene.List(of_type=graphene.String),
    )

    grievance_config = graphene.Field(GrievanceTypeConfigurationGQLType)

    comments = OrderedDjangoFilterConnectionField(
        CommentGQLType,
        orderBy=graphene.List(of_type=graphene.String),
    )

    def resolve_comments(self, info, **kwargs):
        user = info.context.user

        if not (user_associated_with_ticket(user) or user.has_perms(TicketConfig.gql_query_comments_perms)):
            raise PermissionDenied(_("Unauthorized"))

        return gql_optimizer.query(Comment.objects.all(), info)

    def resolve_ticket_details(self, info, **kwargs):
        if not info.context.user.has_perms(TicketConfig.gql_query_tickets_perms):
            raise PermissionDenied(_("unauthorized"))
        return gql_optimizer.query(
            Ticket.objects.filter(*append_validity_filter(**kwargs)).all().order_by('ticket_title', ), info
        )

    def resolve_tickets(self, info, **kwargs):
        """
        Récupère la liste des tickets filtrés selon :
        - Rôles (CNGR, RAC, DEVOPS) : accès complet
        - Autres : tickets assignés, commentés, ou impliquant l'utilisateur dans le JSON workflow
        """
        user = info.context.user

        if not user.has_perms(TicketConfig.gql_query_tickets_perms):
            raise PermissionDenied(_("unauthorized"))

        model = Ticket
        filters = []

        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        show_history = kwargs.get("show_history", False)
        ticket_version = kwargs.get("ticket_version", None)

        # Sélection du dataset de base
        if show_history or ticket_version:
            query = model.history.filter(*filters)
            if ticket_version:
                query = query.filter(version=ticket_version)
            query = query.as_instances()
        else:
            query = model.objects.filter(*filters, is_deleted=False)

        # ---------------------------
        # Gestion des rôles utilisateur
        # ---------------------------
        roles = list(user.user_roles.values_list("role__name", flat=True)) if hasattr(user, "user_roles") else []
        user_roles_upper = [r.upper() for r in roles]

        full_access_roles = {"CNGR", "DEVOPS", "SAUVEGARDES", "SAUV", "SAUVEGARDE"}

        if not user.is_superuser and not (set(user_roles_upper) & full_access_roles):
            username = user.login_name
            fullname = f"{user.other_names} {user.last_name}".strip()

            # 1. Tickets où l’utilisateur est explicitement staff
            q_user = Q(attending_staff=user)

            # 2. Tickets où l'utilisateur apparaît dans le workflow JSON
            #    (filtrage par UUID exact ou par rôle)
            q_json = Q()

            # 3. Ajout de ses rôles dans les clés 'to_role' et 'assignee_role'
            for role_name in user_roles_upper:
            #    q_json |= Q(json_ext__workflow__assignee_role=role_name)
                q_json |= Q(json_ext__workflow__history__contains=[{"to_role": role_name}])

            q_json = Q(Q(json_ext__workflow__history__contains=[{"by": user.username}]) & q_json)
            #q_json = Q(Q(json_ext__workflow__history__contains=[{"to_user_id": str(user.uuid)}]) & q_json)
            # Combine les deux filtres (utilisateur + workflow)
            query = query.filter(q_user | q_json).distinct()

        # ---------------------------
        # Retour final optimisé
        # ---------------------------
        return gql_optimizer.query(query, info)

    def resolve_ticketsStr(self, info, **kwargs):
        """
        Extra steps to perform when Scheme is queried
        """
        # Check if user has permission
        if not info.context.user.has_perms(TicketConfig.gql_query_tickets_perms):
            raise PermissionDenied(_("unauthorized"))
        filters = []

        # Used to specify if user want to see all records including invalid records as history
        show_history = kwargs.get('show_history', False)
        if not show_history:
            filters += append_validity_filter(**kwargs)

        client_mutation_id = kwargs.get("client_mutation_id", None)
        if client_mutation_id:
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        # str = kwargs.get('str')
        # if str is not None:
        #     filters += [Q(code__icontains=str) | Q(name__icontains=str)]

        return gql_optimizer.query(Ticket.objects.filter(*filters).all(), info)

    # def resolve_claim_attachments(self, info, **kwargs):
    #     if not info.context.user.has_perms(TicketConfig.gql_query_tickets_perms):
    #         raise PermissionDenied(_("unauthorized"))


    def resolve_grievance_config(self, info, **kwargs):
        user = info.context.user
        if type(user) is AnonymousUser:
            raise PermissionDenied(_("unauthorized"))
        if not info.context.user.has_perms(TicketConfig.gql_query_tickets_perms):
            raise PermissionDenied(_("unauthorized"))
        return GrievanceTypeConfigurationGQLType()


class Mutation(graphene.ObjectType):
    # === Tickets ============================================================
    create_ticket = CreateTicketMutation.Field()
    update_ticket = UpdateTicketMutation.Field()
    delete_ticket = DeleteTicketMutation.Field()

    # === Commentaires / Résolution ==========================================
    create_comment = CreateCommentMutation.Field()
    resolve_grievance_by_comment = ResolveGrievanceByCommentMutation.Field()

    # === Gestion de cycle de vie ============================================
    reopen_ticket = ReopenTicketMutation.Field()
    escalate_ticket = EscalateTicketMutation.Field()
    escalate_tickets = EscalateTicketsMutation.Field()
    resolve_tickets = ResolveTicketsMutation.Field()

    # === Export =============================================================
    export_selected_tickets = ExportSelectedTicketsMutation.Field()

    # === Dossier décès (Upload multipart) ===================================
    update_ticket_death_dossier = UpdateTicketDeathDossierMutation.Field()


def on_bank_mutation(kwargs, k='uuid'):
    """
    This method is called on signal binding for scheme mutation
    """

    # get uuid from data
    ticket_uuid = kwargs['data'].get('uuid', None)
    if not ticket_uuid:
        return []
    # fetch the scheme object by uuid
    impacted_ticket = Ticket.objects.get(Q(uuid=ticket_uuid))
    # Create a mutation object
    TicketMutation.objects.create(Bank=impacted_ticket, mutation_id=kwargs['mutation_log_id'])
    return []


def on_ticket_mutation(**kwargs):
    uuids = kwargs["data"].get("uuids", [])
    if not uuids:
        uuid = kwargs["data"].get("claim_uuid", None)
        uuids = [uuid] if uuid else []
    if not uuids:
        return []
    impacted_tickets = Ticket.objects.filter(uuid__in=uuids).all()
    for ticket in impacted_tickets:
        TicketMutation.objects.create(Ticket=ticket, mutation_id=kwargs["mutation_log_id"])
    return []


def bind_signals():
    signal_mutation_module_validate[MODULE_NAME].connect(on_ticket_mutation)
