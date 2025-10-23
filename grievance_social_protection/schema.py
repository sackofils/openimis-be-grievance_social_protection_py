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
        - Rôles (CNGR, RAC, ETM, DEVOPS) : accès complet
        - Autres : tickets assignés, commentés, ou impliquant l'utilisateur dans le JSON workflow
        """
        user = info.context.user

        # Vérif permission GraphQL
        if not user.has_perms(TicketConfig.gql_query_tickets_perms):
            raise PermissionDenied(_("unauthorized"))

        filters = []
        model = Ticket

        # Filtrage de base
        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        show_history = kwargs.get("show_history", False)
        ticket_version = kwargs.get("ticket_version", False)

        if show_history or ticket_version:
            if ticket_version:
                filters.append(Q(version=ticket_version))
            query = model.history.filter(*filters).all().as_instances()
        else:
            query = model.objects.filter(*filters, is_deleted=False).all()

        # --- Récupération des rôles de l'utilisateur ---
        roles = list(user.user_roles.values_list("role__name", flat=True)) if hasattr(user, "user_roles") else []
        user_roles_str = [r.upper() for r in roles]

        # Rôles ayant un accès complet
        full_access_roles = {"CNGR", "DEVOPS", "SAUVEGARDES"}

        # --- Application du filtrage par rôle ---
        if not user.is_superuser and not (set(user_roles_str) & full_access_roles):
            username = user.login_name
            fullname = f"{user.other_names} {user.last_name}".strip()

            # Tickets directement liés à l'utilisateur
            q_user = Q(attending_staff=user) #| Q(comments__commenter=user)

            # Tickets où l'utilisateur ou son rôle apparaît dans le workflow JSON
            q_json = Q(json_ext__workflow__history__contains=[{"to_user_id": str(user.uuid)}])

            # Ajout de chaque rôle utilisateur dans le filtre JSON
            #for role_name in user_roles_str:
            #    q_json |= Q(json_ext__workflow__assignee_role = role_name)
            #    q_json |= Q(json_ext__workflow__history__contains=[{"to_role": role_name}])

            # Combinaison globale
            query = query.filter(q_user | q_json).distinct()

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
    create_Ticket = CreateTicketMutation.Field()
    update_Ticket = UpdateTicketMutation.Field()
    delete_Ticket = DeleteTicketMutation.Field()

    create_comment = CreateCommentMutation.Field()

    resolve_grievance_by_comment = ResolveGrievanceByCommentMutation.Field()
    reopen_ticket = ReopenTicketMutation.Field()
    escalate_ticket = EscalateTicketMutation.Field()

    # create_ticket_attachment = CreateTicketAttachmentMutation.Field()
    # update_ticket_attachment = UpdateTicketAttachmentMutation.Field()


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
