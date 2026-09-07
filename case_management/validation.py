import re
from datetime import date

CM_REASON_REQUIRED = 'CM_REASON_REQUIRED'
CM_REASON_TEXT_REQUIRED = 'CM_REASON_TEXT_REQUIRED'
CM_REASON_UNKNOWN = 'CM_REASON_UNKNOWN'
CM_NO_CHANGE = 'CM_NO_CHANGE'
CM_USE_PHONE_MUTATION = 'CM_USE_PHONE_MUTATION'
CM_USE_PAYMENT_MUTATION = 'CM_USE_PAYMENT_MUTATION'
CM_INVALID_PHONE = 'CM_INVALID_PHONE'
CM_ACTIVE_MEMBERS_EXIST = 'CM_ACTIVE_MEMBERS_EXIST'
CM_SUCCESSOR_REQUIRED = 'CM_SUCCESSOR_REQUIRED'
CM_PAYMENT_IN_FLIGHT = 'CM_PAYMENT_IN_FLIGHT'
CM_CONFLICT_STALE_VERSION = 'CM_CONFLICT_STALE_VERSION'
CM_ALREADY_DEACTIVATED = 'CM_ALREADY_DEACTIVATED'
CM_NOT_DEACTIVATED = 'CM_NOT_DEACTIVATED'
CM_DECEASED_NOT_SINGLE_MEMBER = 'CM_DECEASED_NOT_SINGLE_MEMBER'
CM_HOUSEHOLD_INACTIVE = 'CM_HOUSEHOLD_INACTIVE'
CM_INVALID_EFFECTIVE_DATE = 'CM_INVALID_EFFECTIVE_DATE'
CM_SELF_APPROVAL = 'CM_SELF_APPROVAL'
CM_NOT_FOUND = 'CM_NOT_FOUND'
CM_TASK_CREATE_FAILED = 'CM_TASK_CREATE_FAILED'

PHONE_RE = re.compile(r'^(?:\+255|0)[67]\d{8}$')
OTHER = 'OTHER'


class CaseValidationError(Exception):
    """Carries a stable machine code plus optional payload for the client to render."""

    def __init__(self, code, message=None, payload=None):
        self.code = code
        self.payload = payload or {}
        super().__init__(message or code)


def require(condition, code, message=None, payload=None):
    if not condition:
        raise CaseValidationError(code, message, payload)


def validate_version(instance, version):
    """Optimistic lock. HistoryModel.save() also checks, but this returns a typed error first."""
    if version is None:
        return
    require(int(version) == int(instance.version), CM_CONFLICT_STALE_VERSION,
            'Record changed since it was read',
            {'expected': int(version), 'current': int(instance.version),
             'id': str(instance.id)})


def validate_reason(reason_code, reason_text, vocabulary, required, min_text_length):
    if not required:
        return
    require(reason_code, CM_REASON_REQUIRED, 'A reason is required for this change')
    require(reason_code in vocabulary, CM_REASON_UNKNOWN,
            f'Unknown reason code {reason_code}', {'allowed': list(vocabulary)})
    if reason_code == OTHER:
        require(reason_text and len(reason_text.strip()) >= min_text_length,
                CM_REASON_TEXT_REQUIRED,
                f'Describe the reason in at least {min_text_length} characters')


def validate_phone(value):
    require(value and PHONE_RE.match(value.strip()), CM_INVALID_PHONE,
            'Enter a Tanzanian mobile number, e.g. +255712345678 or 0712345678')


def validate_effective_date(effective_date, not_before=None):
    require(isinstance(effective_date, date), CM_INVALID_EFFECTIVE_DATE,
            'An effective date is required')
    require(effective_date <= date.today(), CM_INVALID_EFFECTIVE_DATE,
            'The effective date cannot be in the future')
    if not_before:
        require(effective_date >= not_before, CM_INVALID_EFFECTIVE_DATE,
                'The effective date precedes enrolment',
                {'not_before': not_before.isoformat()})


def validate_no_active_members(active_members):
    require(not active_members, CM_ACTIVE_MEMBERS_EXIST,
            'Deactivate the remaining members first',
            {'members': [
                {'group_individual_id': str(m.id),
                 'individual_id': str(m.individual_id),
                 'name': f'{m.individual.first_name} {m.individual.last_name}'.strip(),
                 'role': m.role}
                for m in active_members]})


def validate_successors(required_groups, successors_by_group):
    missing = [str(g) for g in required_groups if not successors_by_group.get(str(g))]
    require(not missing, CM_SUCCESSOR_REQUIRED,
            'Nominate a successor for each household this member represents',
            {'groups': missing})
