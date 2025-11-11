from datetime import datetime, timedelta
import json

from django.apps import apps
from django.conf import settings

is_unit_test_env = getattr(settings, "IS_UNIT_TEST_ENV", False)

if "opensearch_reports" in apps.app_configs and not is_unit_test_env:
    from opensearch_reports.service import BaseSyncDocument
    from django_opensearch_dsl import fields as opensearch_fields
    from django_opensearch_dsl.registries import registry
    from grievance_social_protection.models import Ticket
    from location.models import Location

    @registry.register_document
    class TicketDocument(BaseSyncDocument):
        """
        Index OpenSearch pour les plaintes (tickets)
        """

        DASHBOARD_NAME = "Grievance"

        # Champs de base
        key = opensearch_fields.KeywordField()
        title = opensearch_fields.KeywordField()
        description = opensearch_fields.TextField()
        code = opensearch_fields.KeywordField()
        status = opensearch_fields.KeywordField()
        category = opensearch_fields.KeywordField()
        sub_category = opensearch_fields.KeywordField()
        sub_category_level1 = opensearch_fields.KeywordField()
        flags = opensearch_fields.KeywordField()
        channel = opensearch_fields.KeywordField()
        resolution = opensearch_fields.TextField()

        # Localisation hiérarchique
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

        sla = opensearch_fields.ObjectField(properties={
            "submitted_at": opensearch_fields.DateField(),
            "due_date": opensearch_fields.DateField(),
            "days_remaining": opensearch_fields.IntegerField(),
            "sla_state": opensearch_fields.KeywordField(),
        })

        class Index:
            name = "ticket"
            settings = {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }

        class Django:
            model = Ticket
            fields = [
                "id"
            ]
            queryset_pagination = 5000

        # =========================
        # LOCALISATION
        # =========================
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
                    elif current.type == "W":
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

        # =========================
        # SLA CALCULÉ (21 jours)
        # =========================
        def prepare_sla(self, instance):
            SLA_DAYS = 21
            WARN_WINDOW = 3

            # Parse du JSON
            json_ext = getattr(instance, "json_ext", {}) or {}
            if isinstance(json_ext, str):
                try:
                    json_ext = json.loads(json_ext)
                except Exception:
                    json_ext = {}

            # Récupération de la date de soumission
            submitted_at = json_ext.get("submitted_at") or instance.date_created
            if not submitted_at:
                return None

            try:
                submitted_dt = (
                    datetime.fromisoformat(submitted_at)
                    if isinstance(submitted_at, str)
                    else submitted_at
                )
            except Exception:
                return None

            # Calcul date d’échéance
            due_date = submitted_dt + timedelta(days=SLA_DAYS)
            today = datetime.now()

            # Calcul du délai restant
            delta = (due_date - today).days

            if delta < 0:
                sla_state = "En depassement"
            elif delta <= WARN_WINDOW:
                sla_state = "En alerte"
            else:
                sla_state = "Dans les délais"

            return {
                "submitted_at": submitted_dt.isoformat(),
                "due_date": due_date.isoformat(),
                "days_remaining": delta,
                "sla_state": sla_state,
            }
