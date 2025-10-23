from django.apps import apps
from django.conf import settings

is_unit_test_env = getattr(settings, 'IS_UNIT_TEST_ENV', False)

if 'opensearch_reports' in apps.app_configs and not is_unit_test_env:
    from opensearch_reports.service import BaseSyncDocument
    from django_opensearch_dsl import fields as opensearch_fields
    from django_opensearch_dsl.registries import registry
    from grievance_social_protection.models import Ticket
    from location.models import Location

    @registry.register_document
    class TicketDocument(BaseSyncDocument):
        DASHBOARD_NAME = 'Grievance'

        key = opensearch_fields.KeywordField()
        title = opensearch_fields.KeywordField()
        description = opensearch_fields.KeywordField()
        code = opensearch_fields.KeywordField()
        attending_staff = opensearch_fields.KeywordField()
        status = opensearch_fields.KeywordField()
        category = opensearch_fields.KeywordField()
        sub_category = opensearch_fields.KeywordField()
        sub_category_level1 = opensearch_fields.KeywordField()
        flags = opensearch_fields.KeywordField()
        channel = opensearch_fields.KeywordField()
        resolution = opensearch_fields.KeywordField()

        location = opensearch_fields.ObjectField(properties={
            "id": opensearch_fields.IntegerField(),
            "code": opensearch_fields.KeywordField(),
            "name": opensearch_fields.KeywordField(),
            "type": opensearch_fields.KeywordField(),
            "region": opensearch_fields.KeywordField(),
            "prefecture": opensearch_fields.KeywordField(),
            "sous_prefecture": opensearch_fields.KeywordField(),
            "district": opensearch_fields.KeywordField(),
        })

        class Index:
            name = 'ticket'
            settings = {
                'number_of_shards': 1,
                'number_of_replicas': 0
            }

        class Django:
            model = Ticket
            fields = [
                'id'
            ]
            queryset_pagination = 5000

        def prepare_location(self, instance):
            loc = instance.location
            if not loc:
                return None

            def get_ancestors(location):
                hierarchy = {}
                current = location
                while current:
                    if current.type == "R":
                        hierarchy["region"] = current.name
                    elif current.type == "D":
                        hierarchy["prefecture"] = current.name
                    elif current.type == "C":
                        hierarchy["sous_prefecture"] = current.name
                    elif current.type == "V":
                        hierarchy["district"] = current.name
                    current = current.parent
                return hierarchy

            ancestors = get_ancestors(loc)
            return {
                "id": loc.id,
                "code": loc.code,
                "name": loc.name,
                "type": loc.type,
                **ancestors,
            }
