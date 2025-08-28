import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)

MODULE_NAME = "grievance_social_protection"

DEFAULT_STRING = 'Default'
# CRON timedelta: {days},{hours}
DEFAULT_TIME_RESOLUTION = '5,0'

DEFAULT_CFG = {
    "default_validations_disabled": False,
    "gql_query_tickets_perms": ["127000"],
    "gql_query_comments_perms": ["127004"],
    "gql_mutation_create_tickets_perms": ["127001"],
    "gql_mutation_update_tickets_perms": ["127002"],
    "gql_mutation_delete_tickets_perms": ["127003"],
    "gql_mutation_create_comment_perms": ["127005"],
    "gql_mutation_resolve_grievance_perms": ["127006"],
    "tickets_attachments_root_path": None,

    "grievance_types": [
        DEFAULT_STRING,
        'Cas sensibles',
        'Cas speciaux',
        'Cas non sensibles'
    ],
    "grievance_categories": {DEFAULT_STRING: DEFAULT_STRING},
    "grievance_sub_categories": {DEFAULT_STRING: DEFAULT_STRING},
    "grievance_flags": [DEFAULT_STRING],
    "grievance_channels": [
        DEFAULT_STRING,
        'Réclamations écrites transmises au Comité de Gestion de Réclamations',
        'Réclamations orales',
        'Dépôt de lettre dans une boîte de réclamation',
        'Appel téléphonique'
    ],
    "default_responses": {DEFAULT_STRING: DEFAULT_STRING},
    "grievance_anonymized_fields": {DEFAULT_STRING: []},
    # CRON timedelta: {days},{hours}
    "resolution_times": DEFAULT_TIME_RESOLUTION,
    "default_resolution": {DEFAULT_STRING: DEFAULT_TIME_RESOLUTION, 'Category A': '4,0', 'Category B': '6,12'},

    "attending_staff_role_ids": [],
    "default_attending_staff_role_ids": {DEFAULT_STRING: [1, 2]},
}


class TicketConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME
    gql_query_tickets_perms = []
    gql_query_comments_perms = []
    gql_mutation_create_tickets_perms = []
    gql_mutation_update_tickets_perms = []
    gql_mutation_delete_tickets_perms = []
    gql_mutation_resolve_grievance_perms = []
    gql_mutation_create_comment_perms = []
    tickets_attachments_root_path = None

    grievance_types = []
    grievance_categories = []
    grievance_sub_categories = {}
    grievance_flags = []
    grievance_channels = []
    default_responses = {}
    grievance_anonymized_fields = {}
    resolution_times = {}
    default_resolution = {}
    attending_staff_role_ids = []
    default_attending_staff_role_ids = {}

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self.__validate_grievance_dict_fields(cfg, 'default_responses')
        self.__validate_grievance_dict_fields(cfg, 'grievance_anonymized_fields')
        self.__validate_grievance_dict_fields(cfg, 'default_resolution')
        self.__validate_grievance_default_resolution_time(cfg)
        self.__load_config(cfg)

    @classmethod
    def __validate_grievance_dict_fields(cls, cfg, field_name):
        def get_grievance_type_options_msg(types):
            types_string = ", ".join(types)
            return logger.info(f'Available grievance types: {types_string}')

        dict_field = cfg.get(field_name, {})
        if not dict_field:
            return

        grievance_types = cfg.get('grievance_types', [])
        if not grievance_types:
            logger.warning('Please specify grievance_types if you want to setup %s.', field_name)

        if not isinstance(dict_field, dict):
            get_grievance_type_options_msg(grievance_types)
            return

        for field_key in dict_field.keys():
            if field_key not in grievance_types:
                logger.warning('%s in %s not in grievance_types', field_key, field_name)
                get_grievance_type_options_msg(grievance_types)

    @classmethod
    def __validate_grievance_default_resolution_time(cls, cfg):
        dict_field = cfg.get("default_resolution", {})
        if not dict_field:
            return
        for key in dict_field:
            value = dict_field[key]
            if value in ['', None]:
                resolution_times = cfg.get("resolution_times", DEFAULT_TIME_RESOLUTION)
                logger.warning(
                    '"%s" has no value for resolution. The default one is taken as "%s".',
                    key,
                    resolution_times
                )
                dict_field[key] = resolution_times
            else:
                if ',' not in value:
                    logger.warning("Invalid input. Configuration should contain two integers "
                                   "representing days and hours, separated by a comma.")
                else:
                    parts = value.split(',')
                    # Parse days and hours
                    days = int(parts[0])
                    hours = int(parts[1])
                    # Validate days and hours
                    if 0 <= days < 99 and 0 <= hours < 24:
                        logger.info(f"Days: {days}, Hours: {hours}")
                    else:
                        logger.warning("Invalid input. Days must be between 0 and 99, "
                                       "and hours must be between 0 and 24.")

    @classmethod
    def __load_config(cls, cfg):
        """
        Load all config fields that match current AppConfig class fields, all custom fields have to be loaded separately
        """
        for field in cfg:
            if hasattr(TicketConfig, field):
                setattr(TicketConfig, field, cfg[field])
