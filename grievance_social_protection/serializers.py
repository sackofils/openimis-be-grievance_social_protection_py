from rest_framework import serializers


class ExportSelectedTicketsSerializer(serializers.Serializer):
    ticket_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
        allow_empty=False,
        help_text="Liste des UUID des tickets à exporter"
    )
    dry_run = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Si vrai, ne génère pas les fichiers mais retourne les groupes détectés"
    )
