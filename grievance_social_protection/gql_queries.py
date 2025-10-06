import graphene
from graphene import ObjectType
from graphene_django import DjangoObjectType
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from core.gql_queries import UserGQLType
from .apps import TicketConfig
from .models import (Ticket, Comment, GrievanceType, GrievanceCategory, GrievanceSubCategory, GrievanceFlag, GrievanceChannel)

from core import prefix_filterset, ExtendedConnection
from .util import model_obj_to_json
from .validations import user_associated_with_ticket


def check_ticket_perms(info):
    if not info.context.user.has_perms(TicketConfig.gql_query_tickets_perms):
        raise PermissionDenied(_("unauthorized"))


def check_comment_perms(info):
    user = info.context.user
    if not (user_associated_with_ticket(user) or user.has_perms(TicketConfig.gql_query_comments_perms)):
        raise PermissionDenied(_("Unauthorized"))


class TicketGQLType(DjangoObjectType):
    # TODO on resolve check filters and remove anonymized so user can't fetch ticket using last_name if not visible
    client_mutation_id = graphene.String()
    reporter = graphene.JSONString()
    reporter_type = graphene.Int()
    reporter_type_name = graphene.String()
    is_history = graphene.Boolean()

    reporter_first_name = graphene.String()
    reporter_last_name = graphene.String()
    reporter_dob = graphene.String()

    @staticmethod
    def resolve_reporter_type(root, info):
        check_ticket_perms(info)
        return root.reporter_type.id if root.reporter_type else None

    @staticmethod
    def resolve_reporter_type_name(root, info):
        check_ticket_perms(info)
        return root.reporter_type.name if root.reporter_type else None

    @staticmethod
    def resolve_reporter(root, info):
        check_ticket_perms(info)
        return model_obj_to_json(root.reporter) if root.reporter else None

    @staticmethod
    def resolve_is_history(root, info):
        check_ticket_perms(info)
        return not root.version == Ticket.objects.get(id=root.id).version

    @staticmethod
    def resolve_reporter_first_name(root, info):
        check_ticket_perms(info)
        if root.reporter_type:
            content_type = ContentType.objects.get_for_model(root.reporter_type.model_class())
            if content_type:
                model_object = content_type.get_object_for_this_type(pk=root.reporter_id)
                if model_object:
                    if root.reporter_type.name == 'individual':
                        return model_object.first_name
                    elif root.reporter_type.name == 'beneficiary':
                        return model_object.individual.first_name
                    elif root.reporter_type.name == 'user':
                        return None
        return None

    @staticmethod
    def resolve_reporter_last_name(root, info):
        check_ticket_perms(info)
        if root.reporter_type:
            content_type = ContentType.objects.get_for_model(root.reporter_type.model_class())
            if content_type:
                model_object = content_type.get_object_for_this_type(pk=root.reporter_id)
                if model_object:
                    if root.reporter_type.name == 'individual':
                        return model_object.last_name
                    elif root.reporter_type.name == 'beneficiary':
                        return model_object.individual.last_name
                    elif root.reporter_type.name == 'user':
                        return None
        return None

    @staticmethod
    def resolve_reporter_dob(root, info):
        check_ticket_perms(info)
        if root.reporter_type:
            content_type = ContentType.objects.get_for_model(root.reporter_type.model_class())
            if content_type:
                model_object = content_type.get_object_for_this_type(pk=root.reporter_id)
                if model_object:
                    if root.reporter_type.name == 'individual':
                        return model_object.dob
                    elif root.reporter_type.name == 'beneficiary':
                        return model_object.individual.dob
                    elif root.reporter_type.name == 'user':
                        return None
        return None

    class Meta:
        model = Ticket
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact", "isnull"],
            "version": ["exact"],
            "key": ["exact", "istartswith", "icontains", "iexact"],
            "code": ["exact", "istartswith", "icontains", "iexact"],
            "title": ["exact", "istartswith", "icontains", "iexact"],
            "description": ["exact", "istartswith", "icontains", "iexact"],
            "status": ["exact", "istartswith", "icontains", "iexact"],
            "priority": ["exact", "istartswith", "icontains", "iexact"],
            "category": ["exact", "istartswith", "icontains", "iexact"],
            "flags": ["exact", "istartswith", "icontains", "iexact"],
            "channel": ["exact", "istartswith", "icontains", "iexact"],
            "resolution": ["exact", "istartswith", "icontains", "iexact"],
            'reporter_id': ["exact"],
            "due_date": ["exact", "istartswith", "icontains", "iexact"],
            "date_of_incident": ["exact", "istartswith", "icontains", "iexact"],
            "date_created": ["exact", "istartswith", "icontains", "iexact"],
            **prefix_filterset("attending_staff__", UserGQLType._meta.filter_fields),
        }

        connection_class = ExtendedConnection

    def resolve_client_mutation_id(self, info):
        ticket_mutation = self.mutations.select_related(
            'mutation').filter(mutation__status=0).first()
        return ticket_mutation.mutation.client_mutation_id if ticket_mutation else None


class CommentGQLType(DjangoObjectType):
    commenter = graphene.JSONString()
    commenter_type = graphene.Int()
    commenter_type_name = graphene.String()

    commenter_first_name = graphene.String()
    commenter_last_name = graphene.String()
    commenter_dob = graphene.String()

    @staticmethod
    def resolve_commenter_type(root, info):
        check_comment_perms(info)
        return root.commenter_type.id if root.commenter_type else None

    @staticmethod
    def resolve_commenter_type_name(root, info):
        check_comment_perms(info)
        return root.commenter_type.name if root.commenter_type else None

    @staticmethod
    def resolve_commenter(root, info):
        check_comment_perms(info)
        return model_obj_to_json(root.commenter) if root.commenter else None

    @staticmethod
    def resolve_commenter_first_name(root, info):
        check_comment_perms(info)
        if root.commenter_type:
            content_type = ContentType.objects.get_for_model(root.commenter_type.model_class())
            if content_type:
                model_object = content_type.get_object_for_this_type(pk=root.commenter_id)
                if model_object:
                    if root.commenter_type.name == 'individual':
                        return model_object.first_name
                    elif root.commenter_type.name == 'beneficiary':
                        return model_object.individual.first_name
                    elif root.commenter_type.name == 'user':
                        return None
        return None

    @staticmethod
    def resolve_commenter_last_name(root, info):
        check_comment_perms(info)
        if root.commenter_type:
            content_type = ContentType.objects.get_for_model(root.commenter_type.model_class())
            if content_type:
                model_object = content_type.get_object_for_this_type(pk=root.commenter_id)
                if model_object:
                    if root.commenter_type.name == 'individual':
                        return model_object.last_name
                    elif root.commenter_type.name == 'beneficiary':
                        return model_object.individual.last_name
                    elif root.commenter_type.name == 'user':
                        return None
        return None

    @staticmethod
    def resolve_commenter_dob(root, info):
        check_comment_perms(info)
        if root.commenter_type:
            content_type = ContentType.objects.get_for_model(root.commenter_type.model_class())
            if content_type:
                model_object = content_type.get_object_for_this_type(pk=root.commenter_id)
                if model_object:
                    if root.commenter_type.name == 'individual':
                        return model_object.dob
                    elif root.commenter_type.name == 'beneficiary':
                        return model_object.individual.dob
                    elif root.commenter_type.name == 'user':
                        return None
        return None

    class Meta:
        model = Comment
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact", "isnull"],
            "comment": ["exact", "istartswith", "icontains", "iexact"],
            "date_created": ["exact", "istartswith", "icontains", "iexact"],
            "is_resolution": ["exact"],
            **prefix_filterset("ticket__", TicketGQLType._meta.filter_fields),
        }

        connection_class = ExtendedConnection


# class TicketAttachmentGQLType(DjangoObjectType):
#     class Meta:
#         model = TicketAttachment
#         interfaces = (graphene.relay.Node,)
#         filter_fields = {
#             "id": ["exact"],
#             "filename": ["exact", "icontains"],
#             "mime_type": ["exact", "icontains"],
#             "url": ["exact", "icontains"],
#             **prefix_filterset("ticket__", TicketGQLType._meta.filter_fields),
#         }
#         connection_class = ExtendedConnection
#
#     @classmethod
#     def get_queryset(cls, queryset, info):
#         queryset = queryset.filter(*filter_validity())
#         return queryset


class AttendingStaffRoleGQLType(ObjectType):
    category = graphene.String()
    role_ids = graphene.List(graphene.String)


class ResolutionTimesByCategoryGQLType(ObjectType):
    category = graphene.String()
    resolution_time = graphene.String()

class CategoriesByTypeGQLType(ObjectType):
    type = graphene.String()
    categories = graphene.List(graphene.String)

class SubCategoriesByCategoryGQL(ObjectType):
    category = graphene.String()
    sub_categories = graphene.List(graphene.String)

class GrievanceSubCategoryGQL(DjangoObjectType):
    class Meta:
        model = GrievanceSubCategory
        fields = ("id", "code", "name", "order", "active")

class GrievanceCategoryGQL(DjangoObjectType):
    subCategories = graphene.List(GrievanceSubCategoryGQL)

    class Meta:
        model = GrievanceCategory
        fields = ("id", "code", "name", "order", "active", "parent")

    def resolve_subCategories(self, info):
        return self.sub_categories.filter(active=True).order_by("order", "name")

class GrievanceTypeGQL(DjangoObjectType):
    subTypes = graphene.List(GrievanceCategoryGQL)

    class Meta:
        model = GrievanceType
        fields = ("id", "code", "name", "is_sensitive", "order", "active")

    def resolve_subTypes(self, info):
        return self.sub_types.filter(active=True).order_by("order", "name")

class GrievanceFlagGQL(DjangoObjectType):
    class Meta:
        model = GrievanceFlag
        fields = ("id", "code", "name", "order", "active")

class GrievanceTypeConfigurationGQLType(ObjectType):
    grievance_types = graphene.List(graphene.String)
    grievance_categories = graphene.List(CategoriesByTypeGQLType)
    grievance_sub_categories = graphene.List(SubCategoriesByCategoryGQL)
    grievance_flags = graphene.List(graphene.String)
    grievance_channels = graphene.List(graphene.String)
    grievance_category_staff_roles = graphene.List(AttendingStaffRoleGQLType)
    grievance_default_resolutions_by_category = graphene.List(ResolutionTimesByCategoryGQLType)
    kobo_ticket_form_url = graphene.String()

    def resolve_grievance_types(self, info):
        return list(GrievanceType.objects.filter(active=True).order_by("order").values_list("name", flat=True))

    def resolve_grievance_flags(self, info):
        return list(GrievanceFlag.objects.filter(active=True).order_by("order").values_list("name", flat=True)) or TicketConfig.grievance_flags

    def resolve_grievance_categories(self, info):
        types = list(
            GrievanceType.objects
            .filter(active=True)
            .order_by("order", "name")
        )
        cats = (
            GrievanceCategory.objects
            .select_related("parent")
            .filter(active=True, parent__active=True)
            .order_by("parent__order", "order", "name")
        )
        # index par type_id
        by_type = {t.id: [] for t in types}
        for c in cats:
            by_type.setdefault(c.parent_id, []).append(c.name)
        return [CategoriesByTypeGQLType(type=t.name, categories=by_type.get(t.id, [])) for t in types]

    def resolve_grievance_sub_categories(self, info):
        cats = list(
            GrievanceCategory.objects
            .filter(active=True, parent__active=True)
            .order_by("parent__order", "order", "name")
        )
        subs = (
            GrievanceSubCategory.objects
            .select_related("parent", "parent__parent")
            .filter(active=True, parent__active=True, parent__parent__active=True)
            .order_by("parent__parent__order", "parent__order", "order", "name")
        )
        by_cat = {c.id: [] for c in cats}
        for sc in subs:
            by_cat.setdefault(sc.parent_id, []).append(sc.name)
        return [SubCategoriesByCategoryGQL(category=c.name, sub_categories=by_cat.get(c.id, [])) for c in cats]

    def resolve_grievance_channels(self, info):
        return list(GrievanceChannel.objects.filter(active=True).order_by("order").values_list("name", flat=True)) or TicketConfig.grievance_channels

    def resolve_grievance_category_staff_roles(self, info):
        category_staff_role_list = []
        for category_key, role_ids in TicketConfig.default_attending_staff_role_ids.items():
            category_staff_role = AttendingStaffRoleGQLType(
                category=category_key,
                role_ids=role_ids
            )
            category_staff_role_list.append(category_staff_role)

        return category_staff_role_list

    def resolve_grievance_default_resolutions_by_category(self, info):
        category_resolution_time_list = []
        for category_key, resolution_time in TicketConfig.default_resolution.items():
            category_resolution_time = ResolutionTimesByCategoryGQLType(
                category=category_key,
                resolution_time=resolution_time
            )
            category_resolution_time_list.append(category_resolution_time)

        return category_resolution_time_list

    def resolve_kobo_ticket_form_url(self, info):
        """Renvoie l’URL du KoboForm actif pour les tickets"""
        try:
            from kobo_connect.models import KoboForm
            form = KoboForm.objects.filter(is_active=True, module="grievance_social_protection").first()
            if form and form.form_uid:
                # return f"{form.api_key.url_kobo.rstrip('/')}/x/{form.form_uid}"
                return f"https://ee-eu.kobotoolbox.org/x/{form.form_uid}"
        except Exception:
            pass
        return None