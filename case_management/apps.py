"""AppConfig for Case Management. Module 29 (rights ``29xxxx``)."""
import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

MODULE_NAME = 'case_management'
IMIS_ADMINISTRATOR_SYSTEM = 64

DEFAULT_HOUSEHOLD_DEACTIVATION_REASONS = [
    'SHIFTED',
    'GRADUATED',
    'COMMITTEE_MEMBER',
    'NOT_ATTENDED_2_CS',
    'DECEASED',           # only valid for a household whose sole member has died
]
DEFAULT_MEMBER_DEACTIVATION_REASONS = ['SHIFTED', 'DECEASED']
DEFAULT_PAYMENT_CHANGE_REASONS = [
    'ACCOUNT_CLOSED', 'ACCOUNT_INVALID', 'FSP_CHANGED', 'BENEFICIARY_REQUEST',
    'NAME_MISMATCH', 'DATA_CORRECTION', 'OTHER',
]

DEFAULT_CONFIG = {
    # Household case console
    'gql_case_search_perms': ['290101'],
    'gql_case_view_perms': ['290102'],
    # Payment change
    'gql_payment_change_search_perms': ['290201'],
    # Account Corrections worklist: accounts that failed MUSE verification.
    'gql_account_correction_search_perms': ['290205'],
    'gql_payment_change_update_perms': ['290203'],
    'gql_payment_phone_update_perms': ['290204'],
    # Deactivation
    'gql_deactivation_search_perms': ['290301'],
    'gql_deactivation_create_perms': ['290302'],
    'gql_reactivation_perms': ['290303'],
    # Follow-up
    'gql_followup_search_perms': ['290401'],
    'gql_followup_create_perms': ['290402'],
    'gql_followup_update_perms': ['290403'],
    # Pending updates / banner
    'gql_pending_update_search_perms': ['290501'],
    'gql_pending_update_decide_perms': ['290502'],

    'enforce_payment_change_reason': False,
    'enable_payment_change_approval': False,
    'enable_followup_sla_sweep': False,
  
    'deactivation_requires_approval': True,
    'payment_change_requires_approval': True,

    'payment_change_approval_flow': 'CASE_PAYMENT_CHANGE',
    'followup_default_sla_days': 7,
    'reason_text_min_length': 10,
    'household_deceased_requires_single_member': True,
    'banner_cache_seconds': 60,

    'household_deactivation_reasons': DEFAULT_HOUSEHOLD_DEACTIVATION_REASONS,
    'member_deactivation_reasons': DEFAULT_MEMBER_DEACTIVATION_REASONS,
    'payment_change_reasons': DEFAULT_PAYMENT_CHANGE_REASONS,

    'seed_rights': True,
}

ALL_RIGHTS = [
    290101, 290102,
    290201, 290203, 290204,
    290301, 290302, 290303,
    290401, 290402, 290403,
    290501, 290502,
]

MATERIAL_PAYMENT_FIELDS = frozenset({
    'account_number', 'fsp_type', 'fsp_name', 'account_name',
})
NON_MATERIAL_PAYMENT_FIELDS = frozenset({'contact_phone', 'is_primary'})


class CaseManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME

    gql_case_search_perms = []
    gql_case_view_perms = []
    gql_payment_change_search_perms = []
    gql_account_correction_search_perms = []
    gql_payment_change_update_perms = []
    gql_payment_phone_update_perms = []
    gql_deactivation_search_perms = []
    gql_deactivation_create_perms = []
    gql_reactivation_perms = []
    gql_followup_search_perms = []
    gql_followup_create_perms = []
    gql_followup_update_perms = []
    gql_pending_update_search_perms = []
    gql_pending_update_decide_perms = []

    enforce_payment_change_reason = False
    enable_payment_change_approval = False
    enable_followup_sla_sweep = False
    deactivation_requires_approval = True
    payment_change_requires_approval = True

    payment_change_approval_flow = 'CASE_PAYMENT_CHANGE'
    followup_default_sla_days = 7
    reason_text_min_length = 10
    household_deceased_requires_single_member = True
    banner_cache_seconds = 60

    household_deactivation_reasons = DEFAULT_HOUSEHOLD_DEACTIVATION_REASONS
    member_deactivation_reasons = DEFAULT_MEMBER_DEACTIVATION_REASONS
    payment_change_reasons = DEFAULT_PAYMENT_CHANGE_REASONS

    seed_rights = True

    def ready(self):
        from core.models import ModuleConfiguration
        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CONFIG)
        self.__load_config(cfg)
        post_migrate.connect(on_post_migrate, sender=self)
        from case_management.signals import bind_service_signals
        bind_service_signals()

    @classmethod
    def __load_config(cls, cfg):
        for field in cfg:
            if hasattr(CaseManagementConfig, field):
                setattr(CaseManagementConfig, field, cfg[field])


def on_post_migrate(sender, **kwargs):
    apps = kwargs.get('apps')
    try:
        if CaseManagementConfig.seed_rights:
            _seed_admin_rights(apps)
    except Exception as exc:
        logger.warning("case_management: rights seeding skipped (%s)", exc)


def _seed_admin_rights(apps):
    Role = apps.get_model('core', 'Role')
    RoleRight = apps.get_model('core', 'RoleRight')
    role = Role.objects.filter(is_system=IMIS_ADMINISTRATOR_SYSTEM, validity_to__isnull=True).first()
    if not role:
        return
    for right_id in ALL_RIGHTS:
        if not RoleRight.objects.filter(role=role, right_id=right_id, validity_to__isnull=True).exists():
            RoleRight.objects.create(role=role, right_id=right_id, audit_user_id=1)
