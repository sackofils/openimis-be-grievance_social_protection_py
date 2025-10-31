from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from grievance_social_protection.models import TicketDeathDossier, Ticket

import uuid
from django.utils.translation import gettext_lazy as _
from rest_framework.views import APIView
from rest_framework import status, permissions

from grievance_social_protection.apps import TicketConfig
from .serializers import ExportSelectedTicketsSerializer

from django.http import FileResponse, Http404
import os
from django.conf import settings

def download_export(request, filename):
    path = os.path.join(settings.MEDIA_ROOT, "exports", filename)
    if not os.path.exists(path):
        raise Http404("File not found")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upload_death_dossier(request):
    """
    Upload d’un ou plusieurs fichiers liés au dossier décès.
    Chaque fichier est optionnel et met à jour les cases 'fournit'.
    """
    ticket_id = request.data.get("ticketId")
    if not ticket_id:
        return Response({"ok": False, "message": "ticketId manquant"}, status=400)

    ticket = get_object_or_404(Ticket, id=ticket_id)
    dossier, _ = TicketDeathDossier.objects.get_or_create(ticket=ticket)

    # Mapping entre clés du form et attributs du modèle
    file_mapping = {
        "file_certificat_deces": ("file_certificat_deces", "certificat_deces"),
        "file_pv_remplacant": ("file_pv_remplacant", "pv_remplacant"),
        "file_id_nouveau_beneficiaire": ("file_id_nouveau_beneficiaire", "id_nouveau_beneficiaire"),
        "file_fiche_engagement": ("file_fiche_engagement", "fiche_engagement"),
    }

    updated_files = []
    for key, (file_field, bool_field) in file_mapping.items():
        uploaded = request.FILES.get(key)
        if uploaded:
            setattr(dossier, file_field, uploaded)
            setattr(dossier, bool_field, True)
            updated_files.append(key)

    dossier.save()

    return Response({
        "ok": True,
        "message": "Fichiers enregistrés avec succès",
        "updated": updated_files,
        "file_urls": {
            "file_certificat_deces": dossier.file_certificat_deces.url if dossier.file_certificat_deces else None,
            "file_pv_remplacant": dossier.file_pv_remplacant.url if dossier.file_pv_remplacant else None,
            "file_id_nouveau_beneficiaire": dossier.file_id_nouveau_beneficiaire.url if dossier.file_id_nouveau_beneficiaire else None,
            "file_fiche_engagement": dossier.file_fiche_engagement.url if dossier.file_fiche_engagement else None,
        },
    })

class ExportSelectedTicketsAPIView(APIView):
    """
    Endpoint REST pour exporter les tickets sélectionnés ou simuler l'export (dry_run).
    """

    #permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ExportSelectedTicketsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user

        if not user.has_perms(TicketConfig.gql_query_tickets_perms):
            return Response({"detail": _("unauthorized")}, status=status.HTTP_403_FORBIDDEN)

        raw_ids = serializer.validated_data.get("ticket_ids", [])
        dry_run = serializer.validated_data.get("dry_run", False)
        valid_ids = []
        for rid in raw_ids:
            try:
                valid_ids.append(uuid.UUID(str(rid)))
            except Exception:
                continue

        if not valid_ids:
            return Response(
                {"detail": _("No valid ticket UUIDs provided")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Imports différés pour éviter les dépendances circulaires
        from .exports import (
            export_selected_tickets_xlsx,
            export_plainte_code_errone_xlsx,
            export_plainte_reactivation_sim_xlsx,
        )

        tickets = list(
            Ticket.objects.filter(id__in=valid_ids).values(
                "id", "resolution", "sub_category", "sub_category_level1"
            )
        )

        if not tickets:
            return Response(
                {"detail": _("No matching tickets found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Regroupement des tickets
        grouped = {"reactivation": [], "code_errone": [], "autre": []}

        for t in tickets:
            resolution = (t.get("resolution") or "").lower()
            sub = (t.get("sub_category_level1") or t.get("sub_category") or "").lower()

            if "réactivation" in resolution or "reactivation" in resolution:
                grouped["reactivation"].append(t["id"])
            elif (
                "réinitialisation" in resolution
                or "code erroné" in resolution
                or "code errone" in resolution
            ):
                grouped["code_errone"].append(t["id"])
            elif "décès" in sub or "deces" in sub:
                grouped["autre"].append(t["id"])

        # Mode simulation : on ne génère rien
        if dry_run:
            total = sum(len(v) for v in grouped.values())
            return Response(
                {
                    "success": True,
                    "dry_run": True,
                    "groups": {
                        "reactivation": len(grouped["reactivation"]),
                        "code_errone": len(grouped["code_errone"]),
                        "autre": len(grouped["autre"]),
                    },
                    "count": total,
                    "message": str(_("Dry run completed — no files generated.")),
                },
                status=status.HTTP_200_OK,
            )

        # Export réel
        file_urls = []
        if grouped["reactivation"]:
            file_urls.append(export_plainte_reactivation_sim_xlsx(grouped["reactivation"]))
        if grouped["code_errone"]:
            file_urls.append(export_plainte_code_errone_xlsx(grouped["code_errone"]))
        if grouped["autre"]:
            file_urls.append(export_selected_tickets_xlsx(grouped["autre"]))

        # Marque les tickets comme exportés
        Ticket.objects.filter(id__in=valid_ids).update(is_exported=True)

        return Response(
            {
                "success": True,
                "dry_run": False,
                "files": file_urls,
                "message": str(_("Exports generated successfully")),
            },
            status=status.HTTP_200_OK,
        )
