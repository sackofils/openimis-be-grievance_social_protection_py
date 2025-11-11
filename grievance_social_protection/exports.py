import os
import io
import json
import openpyxl
import pandas as pd
from django.conf import settings
from django.utils.timezone import localtime
from grievance_social_protection.models import Ticket, TicketDeathDossier
from individual.models import Individual

def add_dashes(input_string):
    """
    Inserts a dash every 3 characters into a string.

    Args:
        input_string: The string to modify.

    Returns:
        The modified string with dashes.
    """
    # Create a list of 3-character chunks using a list comprehension
    if input_string is None:
        return ""
    chunks = [input_string[i:i + 3] for i in range(0, len(input_string), 3)]

    # Join the chunks with a dash
    return '-'.join(chunks)

def export_selected_tickets_xlsx(ids):
    """
    Génère un fichier Excel des tickets (cas de décès ou autres) selon une liste d'IDs fournie.
    Combine les infos du Ticket et du TicketDeathDossier.
    Retourne l'URL du fichier généré dans /media/exports/.
    """

    # --- Sous-fonction pour la hiérarchie géographique ---
    def prepare_location(location):
        if not location:
            return {}
        def get_ancestors(loc):
            hierarchy = {}
            current = loc
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
        ancestors = get_ancestors(location)
        return {
            "location_code": location.code,
            "location_name": location.name,
            **ancestors,
        }

    # --- Chargement des tickets + dossiers liés ---
    qs = Ticket.objects.filter(id__in=ids).select_related("location", "attending_staff", "death_dossier")

    data = []
    for idx, t in enumerate(qs, start=1):
        death = getattr(t, "death_dossier", None)
        json_ext = t.json_ext or {}
        loc_info = prepare_location(t.location)
        individual = None
        if t.household_code:
            individual = Individual.objects.filter(json_ext__id_ben_principal=add_dashes(t.household_code)).first()

        data.append({
            "N°": idx,
            "Code": add_dashes(t.household_code) or "",
            "Région": loc_info.get("region", ""),
            "Préfecture": loc_info.get("prefecture", ""),
            "Sous-préfecture": loc_info.get("sous_prefecture", ""),
            "District": loc_info.get("district", ""),
            "NON BENEFICIAIRE": str(individual) if individual else "",
            "Sexe": individual.json_ext.get("sexe_bp", "") if individual else "",
            # "Titre": t.title,
            # "Catégorie": t.category,
            # "Sous-catégorie": t.sub_category,
            # "Sous-niveau": t.sub_category_level1,
            # "Statut": t.status,
            # "Priorité": t.priority,
            # "Agent traitant": getattr(t.attending_staff, "username", ""),
            # "Téléphone plaignant": t.reporter_phone or json_ext.get("telephonePrenomReclamantExterne", ""),
            # "Date d'incident": t.date_of_incident.strftime("%d/%m/%Y") if t.date_of_incident else "",
            # "Créé le": t.date_created.strftime("%d/%m/%Y") if t.date_created else "",
            # "Échéance": t.due_date.strftime("%d/%m/%Y") if t.due_date else "",
            "Certificat de décès": "Oui" if getattr(death, "certificat_deces", False) else "Non",
            "PV de désignation du remplaçant": "Oui" if getattr(death, "pv_remplacant", False) else "Non",
            "Pièce d'identité du nouveau bénéficiare": "Oui" if getattr(death, "id_nouveau_beneficiaire", False) else "Non",
            "Fiche d'engagement du nouveau bénéficiaire": "Oui" if getattr(death, "fiche_engagement", False) else "Non",
            "Resultat de l'analyse": "DOSSIER AU COMPLET" if getattr(death, "complete", False) else "DOSSIER INCOMPLET",
            "ID nouveau bénéficiaire": getattr(death, "code_beneficiaire", ""),
            "Nom nouveau bénéficiaire": f'{getattr(death, "prenom_beneficiaire", "")} {getattr(death, "nom_beneficiaire", "")}',
            "Sexe du nouveau bénéficiaire": getattr(death, "sexe_beneficiaire", ""),
        })

    df = pd.DataFrame(data)

    if df.empty:
        df = pd.DataFrame([{"info": "Aucune donnée disponible"}])

    # --- Sauvegarde du fichier ---
    out_dir = os.path.join(settings.MEDIA_ROOT, "exports")
    os.makedirs(out_dir, exist_ok=True)

    filename = "export-plaintes-cas-deces.xlsx"
    path = os.path.join(out_dir, filename)

    df.to_excel(path, index=False)

    # --- Retourne l’URL du fichier ---
    return f"{filename}"


def export_plainte_code_errone_xlsx(ids):
    """
    Génère un fichier Excel pour les plaintes 'CODE ERRONÉ' selon la structure du canevas fourni.
    Retourne l'URL du fichier généré.
    """

    # --- Sous-fonction pour la hiérarchie géographique ---
    def prepare_location(location):
        if not location:
            return {}
        def get_ancestors(loc):
            hierarchy = {}
            current = loc
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
        ancestors = get_ancestors(location)
        return ancestors

    # --- Chargement des tickets sélectionnés ---
    qs = Ticket.objects.filter(id__in=ids).select_related("location")

    data = []
    for idx, t in enumerate(qs, start=1):
        json_ext = t.json_ext or {}
        loc_info = prepare_location(t.location)
        individual = None
        if t.household_code:
            individual = Individual.objects.filter(json_ext__id_ben_principal=add_dashes(t.household_code)).first()

        data.append({
            "N°": idx,
            "Région": loc_info.get("region", ""),
            "Préfecture": loc_info.get("prefecture", ""),
            "Sous-préfecture": loc_info.get("sous_prefecture", ""),
            "District": loc_info.get("district", ""),
            "Description de la plainte": t.title,
            "Solutions proposées": t.resolution,
            "code_menage_membre": t.household_code,
            "numero_paie": individual.json_ext.get("numero_paie", "") if individual else "",
            "Date de creation": json_ext.get("submitted_at", ""),
            "Nom": json_ext.get("organisme_nom", "ANIES-GUINEE ANIES"),
            "Grade": json_ext.get("organisme_grade", "Normal Subscriber"),
            "Conformité": json_ext.get("conformite", "Oui"),
        })

    df = pd.DataFrame(data)

    if df.empty:
        df = pd.DataFrame([{"info": "Aucune donnée disponible"}])

    # --- Sauvegarde dans media/exports ---
    out_dir = os.path.join(settings.MEDIA_ROOT, "exports")
    os.makedirs(out_dir, exist_ok=True)

    filename = "Export-plaintes-code_errones.xlsx"
    path = os.path.join(out_dir, filename)
    df.to_excel(path, index=False)

    # --- Retourne l’URL ---
    return f"{filename}"

def export_plainte_reactivation_sim_xlsx(ids):
    """
    Génère un fichier Excel pour les plaintes 'RÉACTIVATION DE LA SIM' selon le canevas fourni.
    Retourne l'URL du fichier généré.
    """

    # --- Sous-fonction pour la hiérarchie géographique ---
    def prepare_location(location):
        if not location:
            return {}
        def get_ancestors(loc):
            hierarchy = {}
            current = loc
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
        ancestors = get_ancestors(location)
        return ancestors

    # --- Chargement des tickets concernés ---
    qs = Ticket.objects.filter(id__in=ids).select_related("location")

    data = []
    for idx, t in enumerate(qs, start=1):
        json_ext = t.json_ext or {}
        loc_info = prepare_location(t.location)
        individual = None
        if t.household_code:
            individual = Individual.objects.filter(json_ext__id_ben_principal=add_dashes(t.household_code)).first()

        data.append({
            "N°": idx,
            "Région": loc_info.get("region", ""),
            "Prefecture": loc_info.get("prefecture", ""),
            "Sous-préfecture": loc_info.get("sous_prefecture", ""),
            "District": loc_info.get("district", ""),
            "Description de la plainte": t.title,
            "Solutions proposées": t.resolution,
            "code_menage_membre": t.household_code,
            "Numero orange": individual.json_ext.get("tel_1", "") if individual else "",
            "Operateur": individual.json_ext.get("operateur", "") if individual else "",
            "IMSI": json_ext.get("imsi", ""),
            "Observations": json_ext.get("observations", ""),
        })

    df = pd.DataFrame(data)

    if df.empty:
        df = pd.DataFrame([{"info": "Aucune donnée disponible"}])

    # --- Sauvegarde du fichier dans /media/exports ---
    out_dir = os.path.join(settings.MEDIA_ROOT, "exports")
    os.makedirs(out_dir, exist_ok=True)

    filename = "Export-plaintes_reactivation_sim.xlsx"
    path = os.path.join(out_dir, filename)
    df.to_excel(path, index=False)

    return f"{filename}"
