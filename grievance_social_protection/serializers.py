from rest_framework import serializers


class ExportSelectedTicketsSerializer(serializers.Serializer):
    ticket_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
        allow_empty=True,
        help_text="Liste des UUID des tickets à exporter"
    )
    dry_run = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Si vrai, ne génère pas les fichiers mais retourne les groupes détectés"
    )
    select_all = serializers.BooleanField(required=False, default=False)
    filters = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    def validate(self, data):
        select_all = data.get("select_all", False)
        ticket_ids = data.get("ticket_ids", [])

        # Si pas en mode global, on exige des IDs
        if not select_all and not ticket_ids:
            raise serializers.ValidationError({
                "ticket_ids": _("Cette liste ne peut pas être vide.")
            })

        return data