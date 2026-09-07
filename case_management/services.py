"""Case management services.

Business rules live here, not in the GraphQL layer: mobile queries the same schema and would
otherwise bypass them.
"""
import contextvars
import logging
import uuid
from contextlib import contextmanager
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from core.services.utils import check_authentication, output_exception, output_result_success
from core.signals import register_service_signal

from case_management.apps import (
    MATERIAL_PAYMENT_FIELDS, NON_MATERIAL_PAYMENT_FIELDS, CaseManagementConfig,
)
from case_management.models import (
    Channel, ChangeType, DeactivationMode, FollowUpRemark, FollowUpStatus,
    HouseholdDeactivation, MemberDeactivation, PaymentChangeAudit, PendingDataUpdate,
    PendingStatus, Severity, UpdateType,
)
from case_management import validation as v

logger = logging.getLogger(__name__)

TRACKED_PAYMENT_FIELDS = MATERIAL_PAYMENT_FIELDS | NON_MATERIAL_PAYMENT_FIELDS

_SINGLE_FIELD_CHANGE_TYPE = {
    'account_number': ChangeType.ACCOUNT_NUMBER,
    'fsp_type': ChangeType.PROVIDER,
    'fsp_name': ChangeType.PROVIDER,
    'account_name': ChangeType.ACCOUNT_NAME,
    'contact_phone': ChangeType.CONTACT_PHONE,
    'is_primary': ChangeType.PRIMARY_FLAG,
}

_IN_CASE_MANAGEMENT = contextvars.ContextVar('case_management_active', default=False)


def in_case_management():
    return _IN_CASE_MANAGEMENT.get()


@contextmanager
def _owning_the_change():
    """Marks writes this module performs so the signal consumer does not double-audit them."""
    token = _IN_CASE_MANAGEMENT.set(True)
    try:
        yield
    finally:
        _IN_CASE_MANAGEMENT.reset(token)




def classify(before, after):
    """Diff two payment field dicts. Returns (change_type, is_material, changed_fields)."""
    changed = {}
    for field in TRACKED_PAYMENT_FIELDS:
        if field not in after:
            continue
        old, new = before.get(field), after.get(field)
        if old != new:
            changed[field] = {'before': old, 'after': new}
    if not changed:
        return None, False, {}
    is_material = bool(set(changed) & MATERIAL_PAYMENT_FIELDS)
    if len(changed) > 1:
        change_type = ChangeType.MULTIPLE
    else:
        change_type = _SINGLE_FIELD_CHANGE_TYPE[next(iter(changed))]
    return change_type, is_material, changed


def resolve_channel(explicit=None, request=None):
    if explicit:
        return explicit
    if request is not None:
        agent = (request.META.get('HTTP_USER_AGENT') or '').lower()
        if 'okhttp' in agent or 'dart' in agent or 'coremis-mobile' in agent:
            return Channel.MOBILE
    return Channel.WEB


def _service_result(fn):
    """Turn CaseValidationError into the openIMIS {'success': False, ...} envelope."""
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except v.CaseValidationError as exc:
            out = output_exception(model_name=fn.__qualname__, method=fn.__name__, exception=exc)
            out['code'] = exc.code
            out['payload'] = exc.payload
            return out
        except Exception as exc:
            logger.exception("case_management.%s failed", fn.__name__)
            return output_exception(model_name=fn.__qualname__, method=fn.__name__, exception=exc)
    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper


class PaymentChangeService:
    """Sole writer of PaymentChangeAudit."""

    def __init__(self, user):
        self.user = user

    def _queue_for_approval(self, entity, payload, summary):
        """Propose-then-apply: park the change as a tasks_management Task, change nothing yet.

        This is the shape the mobile app already uses (DATA_UPDATE_REQUESTS carries
        previousPayload/proposedPayload and is applied on review), and it is the safer order for
        a change that redirects money.
        """
        from django.contrib.contenttypes.models import ContentType
        from tasks_management.services import TaskService

        result = TaskService(self.user).create({
            'source': 'case_management',
            'entity_id': str(entity.pk),
            'entity_type': ContentType.objects.get_for_model(entity.__class__),
            'business_event': PAYMENT_CHANGE_EVENT,
            'business_status': {},
            'data': payload,
        })
        if not result.get('success'):
            raise v.CaseValidationError('CM_TASK_CREATE_FAILED', result.get('detail'))
        return output_result_success({
            'queued': True,
            'task_id': (result.get('data') or {}).get('id'),
            **summary,
        })

    @check_authentication
    @register_service_signal('case_payment_service.update_details')
    @_service_result
    def update_details(self, payment_account_id, fields, reason_code=None, reason_text=None,
                       version=None, channel=None, request=None, _approved=False):
        from tasaf_payment.models import PaymentAccount, PreAuditStatus, VerificationStatus

        account = PaymentAccount.objects.filter(id=payment_account_id, is_deleted=False).first()
        v.require(account, v.CM_NOT_FOUND, 'Payment account not found')
        v.validate_version(account, version)
        v.require('contact_phone' not in fields, v.CM_USE_PHONE_MUTATION,
                  'Use the phone update to change contact_phone')

        before = {f: getattr(account, f) for f in TRACKED_PAYMENT_FIELDS}
        change_type, is_material, changed = classify(before, fields)
        v.require(changed, v.CM_NO_CHANGE, 'Nothing was changed')

        v.validate_reason(
            reason_code, reason_text,
            CaseManagementConfig.payment_change_reasons,
            required=is_material and CaseManagementConfig.enforce_payment_change_reason,
            min_text_length=CaseManagementConfig.reason_text_min_length)

        if (is_material and CaseManagementConfig.payment_change_requires_approval
                and not _approved):
            return self._queue_for_approval(
                account,
                {'payment_account_id': str(payment_account_id),
                 'fields': {k: d['after'] for k, d in changed.items()},
                 'previous': {k: d['before'] for k, d in changed.items()},
                 'reason_code': reason_code, 'reason_text': reason_text,
                 'channel': str(resolve_channel(channel, request))},
                {'change_type': str(change_type), 'is_material': True})

        with _owning_the_change(), transaction.atomic():
            previous_verification = account.verification_status
            previous_pre_audit = account.pre_audit_status
            for field, delta in changed.items():
                setattr(account, field, delta['after'])
            if is_material:
                account.verification_status = VerificationStatus.PENDING
                account.pre_audit_status = PreAuditStatus.PENDING
            account.save(user=self.user)

            audit = PaymentChangeAudit(
                payment_account=account,
                group_beneficiary_id=account.group_beneficiary_id,
                change_type=change_type,
                is_material=is_material,
                changed_fields=changed,
                reason_code=reason_code,
                reason_text=reason_text,
                channel=resolve_channel(channel, request),
                previous_verification_status=previous_verification,
                previous_pre_audit_status=previous_pre_audit,
            )
            audit.save(user=self.user)

            approval_request = None
            if is_material and CaseManagementConfig.enable_payment_change_approval:
                approval_request = self._open_approval(account, audit)
                if approval_request:
                    audit.approval_request = approval_request
                    audit.save(user=self.user)
            if is_material:
                PendingUpdateService(self.user).open(
                    target=account, update_type=UpdateType.PAYMENT_CHANGE,
                    severity=Severity.CRITICAL,
                    group=self._group_of(account),
                    approval_request=approval_request,
                    summary={'change_type': str(change_type),
                             'fields': sorted(changed),
                             'reason_code': reason_code})
        return output_result_success({'id': str(audit.id), 'is_material': is_material,
                                      'change_type': str(change_type)})

    @check_authentication
    @register_service_signal('case_payment_service.update_phone')
    @_service_result
    def update_phone(self, payment_account_id, contact_phone, version=None,
                     channel=None, request=None):
        from tasaf_payment.models import PaymentAccount

        account = PaymentAccount.objects.filter(id=payment_account_id, is_deleted=False).first()
        v.require(account, v.CM_NOT_FOUND, 'Payment account not found')
        v.validate_version(account, version)
        v.validate_phone(contact_phone)

        phone = contact_phone.strip()
        v.require(phone != (account.contact_phone or ''), v.CM_NO_CHANGE, 'Nothing was changed')

        with _owning_the_change(), transaction.atomic():
            changed = {'contact_phone': {'before': account.contact_phone, 'after': phone}}
            account.contact_phone = phone
            account.save(user=self.user)

            audit = PaymentChangeAudit(
                payment_account=account,
                group_beneficiary_id=account.group_beneficiary_id,
                change_type=ChangeType.CONTACT_PHONE,
                is_material=False,
                changed_fields=changed,
                channel=resolve_channel(channel, request),
            )
            audit.save(user=self.user)

            PendingUpdateService(self.user).open(
                target=account, update_type=UpdateType.PAYMENT_CHANGE,
                severity=Severity.INFO, group=self._group_of(account),
                summary={'change_type': str(ChangeType.CONTACT_PHONE)})
        return output_result_success({'id': str(audit.id), 'is_material': False})

    def _open_approval(self, account, audit):
        from approval.services import ApprovalService

        result = ApprovalService(self.user).request_approval(
            entity=account,
            flow_code=CaseManagementConfig.payment_change_approval_flow,
            summary={'change_type': str(audit.change_type),
                     'fields': sorted(audit.changed_fields or {}),
                     'reason_code': audit.reason_code,
                     'audit_id': str(audit.id)})
        if not result.get('success'):
            logger.warning("case_management: could not open approval (%s)", result.get('detail'))
            return None
        from approval.models import ApprovalRequest
        return ApprovalRequest.objects.filter(id=(result.get('data') or {}).get('id')).first()

    @staticmethod
    def _group_of(account):
        gb = account.group_beneficiary
        return gb.group if gb else None


PAYMENT_CHANGE_EVENT = 'case_management.payment_change'
DEACTIVATE_MEMBER_EVENT = 'case_management.deactivate_member'
DEACTIVATE_HOUSEHOLD_EVENT = 'case_management.deactivate_household'


class HouseholdCaseService:
    """Deactivation, reactivation and representative changes. Delegates state to owning services."""

    DECEASED = 'DECEASED'

    def __init__(self, user):
        self.user = user

    def _queue_for_approval(self, business_event, entity, payload, summary):
        """Maker-checker: park the change as a tasks_management Task instead of applying it.

        The task carries the whole payload; `on_deactivation_task_complete` replays it against the
        service with approval bypassed once a checker completes the task. Nothing is written to the
        household or member until then.
        """
        from django.contrib.contenttypes.models import ContentType
        from tasks_management.services import TaskService

        result = TaskService(self.user).create({
            'source': 'case_management',
            'entity_id': str(entity.pk),
            'entity_type': ContentType.objects.get_for_model(entity.__class__),
            'business_event': business_event,
            'business_status': {},
            'data': {k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                     for k, v in payload.items()},
        })
        if not result.get('success'):
            raise v.CaseValidationError('CM_TASK_CREATE_FAILED', result.get('detail'))
        return output_result_success({
            'queued': True,
            'task_id': (result.get('data') or {}).get('id'),
            **summary,
        })

    @check_authentication
    @register_service_signal('case_household_service.deactivate_household')
    @_service_result
    def deactivate_household(self, group_id, mode, reason_code, effective_date,
                             reason_text=None, version=None, _approved=False):
        from individual.models import Group, GroupIndividual
        from social_protection.models import GroupBeneficiary

        group = Group.objects.filter(id=group_id, is_deleted=False).first()
        v.require(group, v.CM_NOT_FOUND, 'Household not found')
        v.validate_version(group, version)
        v.validate_reason(reason_code, reason_text,
                          CaseManagementConfig.household_deactivation_reasons,
                          required=True,
                          min_text_length=CaseManagementConfig.reason_text_min_length)
        v.validate_effective_date(effective_date)

        beneficiary = GroupBeneficiary.objects.filter(
            group=group, is_deleted=False).exclude(status='SUSPENDED').first()
        v.require(beneficiary, v.CM_ALREADY_DEACTIVATED, 'Household is not active')

        if (reason_code == 'DECEASED'
                and CaseManagementConfig.household_deceased_requires_single_member):
            member_count = GroupIndividual.objects.filter(
                group=group, is_deleted=False, is_active=True).count()
            v.require(member_count <= 1, v.CM_DECEASED_NOT_SINGLE_MEMBER,
                      'DECEASED applies only to a household with a single member; '
                      'deactivate the individual members instead',
                      {'member_count': member_count})

        if mode == DeactivationMode.PERMANENT:
            active = list(GroupIndividual.objects
                          .filter(group=group, is_deleted=False, is_active=True)
                          .select_related('individual'))
            v.validate_no_active_members(active)
            self._require_no_payment_in_flight(group)

        if CaseManagementConfig.deactivation_requires_approval and not _approved:
            return self._queue_for_approval(
                DEACTIVATE_HOUSEHOLD_EVENT, group,
                {'group_id': str(group_id), 'mode': mode, 'reason_code': reason_code,
                 'reason_text': reason_text, 'effective_date': effective_date},
                {'group_code': group.code, 'mode': str(mode)})

        with transaction.atomic():
            previous_status = beneficiary.status
            self._set_beneficiary_status(beneficiary, 'SUSPENDED')
            record = HouseholdDeactivation(
                group=group, group_beneficiary=beneficiary, mode=mode,
                reason_code=reason_code, reason_text=reason_text,
                effective_date=effective_date, previous_status=previous_status)
            record.save(user=self.user)
        return output_result_success({'id': str(record.id), 'mode': str(mode)})

    @check_authentication
    @register_service_signal('case_household_service.reactivate_household')
    @_service_result
    def reactivate_household(self, group_id, reason_text=None):
        from individual.models import Group

        group = Group.objects.filter(id=group_id, is_deleted=False).first()
        v.require(group, v.CM_NOT_FOUND, 'Household not found')
        record = (HouseholdDeactivation.objects
                  .filter(group=group, reactivated_at__isnull=True, is_deleted=False)
                  .order_by('-date_created').first())
        v.require(record, v.CM_NOT_DEACTIVATED, 'Household is not deactivated')

        with transaction.atomic():
            if record.group_beneficiary_id:
                self._set_beneficiary_status(record.group_beneficiary, record.previous_status)
            record.reactivated_at = timezone.now()
            record.reactivated_by = self.user
            record.reactivation_reason = reason_text
            record.save(user=self.user)
        return output_result_success({'id': str(record.id),
                                      'restored_status': record.previous_status})

    @check_authentication
    @register_service_signal('case_household_service.reactivate_member')
    @_service_result
    def reactivate_member(self, group_individual_id, reason_text=None):
        record = (MemberDeactivation.objects
                  .filter(group_individual_id=group_individual_id,
                          reactivated_at__isnull=True, is_deleted=False)
                  .order_by('-date_created').first())
        v.require(record, v.CM_NOT_DEACTIVATED, 'Member is not deactivated')

        with transaction.atomic():
            if record.propagation_id:
                # a DECEASED cascade is reversed as one unit, never member by member
                return self._reverse(record.propagation_id, reason_text)
            record.reactivation_reason = reason_text
            self._restore_membership(record)
        return output_result_success({'id': str(record.id), 'restored': 1})

    def _reverse(self, propagation_id, reason_text):
        records = list(MemberDeactivation.objects.filter(
            propagation_id=propagation_id, reactivated_at__isnull=True, is_deleted=False))
        individual = records[0].individual if records else None
        for record in records:
            record.reactivation_reason = reason_text
            self._restore_membership(record)
        if individual:
            json_ext = dict(individual.json_ext or {})
            json_ext.pop('deceased', None)
            individual.json_ext = json_ext
            individual.save(user=self.user)
        return output_result_success({'restored': len(records),
                                      'propagation_id': str(propagation_id)})

    @check_authentication
    @register_service_signal('case_household_service.deactivate_member')
    @_service_result
    def deactivate_member(self, group_individual_id, reason_code, effective_date,
                          reason_text=None, date_of_death=None, successors=None, version=None,
                          person_level=False, _approved=False):
        from individual.models import GroupIndividual

        membership = GroupIndividual.objects.filter(
            id=group_individual_id, is_deleted=False).select_related('individual').first()
        v.require(membership, v.CM_NOT_FOUND, 'Member not found')
        v.validate_version(membership, version)
        v.validate_reason(reason_code, reason_text,
                          CaseManagementConfig.member_deactivation_reasons,
                          required=True,
                          min_text_length=CaseManagementConfig.reason_text_min_length)
        v.validate_effective_date(effective_date)

        if CaseManagementConfig.deactivation_requires_approval and not _approved:
            return self._queue_for_approval(
                DEACTIVATE_MEMBER_EVENT, membership,
                {'group_individual_id': str(group_individual_id), 'reason_code': reason_code,
                 'reason_text': reason_text, 'effective_date': effective_date,
                 'date_of_death': date_of_death, 'successors': successors or {},
                 'person_level': person_level},
                {'member': f'{membership.individual.first_name} '
                           f'{membership.individual.last_name}'.strip()})


        if reason_code == self.DECEASED or person_level:
            return self._deactivate_person(
                membership.individual, reason_code, reason_text, effective_date,
                date_of_death, successors or {})

        successors = successors or {}
        if self._is_representative(membership) and self._has_successor_candidate(membership):
            v.validate_successors([membership.group_id], successors)

        with transaction.atomic():
            record = self._end_membership(
                membership, reason_code, reason_text, effective_date,
                successor_id=successors.get(str(membership.group_id)),
                propagation_id=None, is_person_level=False)
        return output_result_success({'id': str(record.id), 'memberships_ended': 1})

    def _deactivate_person(self, individual, reason_code, reason_text, effective_date,
                           date_of_death, successors):
        """Death is a fact about the person: end every membership under one propagation_id."""
        from individual.models import GroupIndividual
        from social_protection.models import Beneficiary

        memberships = list(GroupIndividual.objects
                           .filter(individual=individual, is_deleted=False, is_active=True)
                           .select_related('group'))
        v.require(memberships, v.CM_NOT_FOUND, 'No active membership for this person')

        represented = [m.group_id for m in memberships
                       if self._is_representative(m) and self._has_successor_candidate(m)]
        v.validate_successors(represented, successors)

        propagation_id = uuid.uuid4()
        with transaction.atomic():
            records = [
                self._end_membership(
                    m, reason_code, reason_text, effective_date,
                    successor_id=successors.get(str(m.group_id)),
                    propagation_id=propagation_id, is_person_level=True,
                    date_of_death=date_of_death)
                for m in memberships
            ]
            individual.json_ext = {**(individual.json_ext or {}), 'deceased': {
                'date_of_death': date_of_death.isoformat() if date_of_death else None,
                'recorded_at': timezone.now().isoformat(),
                'propagation_id': str(propagation_id),
                'blocks_reenrolment': True,
            }}
            individual.save(user=self.user)

            suspended = 0
            for b in Beneficiary.objects.filter(individual=individual, is_deleted=False).exclude(
                    status='SUSPENDED'):
                self._set_beneficiary_status(b, 'SUSPENDED')
                suspended += 1

        return output_result_success({
            'propagation_id': str(propagation_id),
            'memberships_ended': len(records),
            'beneficiaries_suspended': suspended,
        })

    @check_authentication
    @_service_result
    def reverse_propagation(self, propagation_id, reason_text=None):
        """Undo a DECEASED cascade recorded in error, as one unit."""
        records = list(MemberDeactivation.objects.filter(
            propagation_id=propagation_id, reactivated_at__isnull=True, is_deleted=False))
        v.require(records, v.CM_NOT_DEACTIVATED, 'No open cascade with that id')

        with transaction.atomic():
            individual = records[0].individual
            for record in records:
                self._restore_membership(record)
            json_ext = dict(individual.json_ext or {})
            json_ext.pop('deceased', None)
            individual.json_ext = json_ext
            individual.save(user=self.user)
        return output_result_success({'restored': len(records)})

    @check_authentication
    @_service_result
    def update_representative(self, group_individual_id, role=None, recipient_type=None,
                              reason_text=None, version=None):
        """Wraps the individual module's alignment service to add reason and audit."""
        from individual.models import GroupIndividual
        from individual.services import GroupAndGroupIndividualAlignmentService

        membership = GroupIndividual.objects.filter(
            id=group_individual_id, is_deleted=False).first()
        v.require(membership, v.CM_NOT_FOUND, 'Member not found')
        v.validate_version(membership, version)

        alignment = GroupAndGroupIndividualAlignmentService(self.user)
        with transaction.atomic():
            if role:
                alignment.handle_head_change(membership.id, role, membership.group_id)
            if recipient_type:
                alignment.handle_primary_recipient_change(
                    membership.id, recipient_type, membership.group_id)
            PendingUpdateService(self.user).open(
                target=membership, update_type=UpdateType.REPRESENTATIVE_CHANGE,
                severity=Severity.WARNING, group=membership.group,
                summary={'role': role, 'recipient_type': recipient_type,
                         'reason': reason_text})
        return output_result_success({'id': str(membership.id)})

    def _end_membership(self, membership, reason_code, reason_text, effective_date,
                        successor_id, propagation_id, is_person_level, date_of_death=None):
        from individual.models import GroupIndividual
        from individual.services import GroupIndividualService

        was_representative = self._is_representative(membership)
        record = MemberDeactivation(
            group_individual=membership,
            individual=membership.individual,
            group=membership.group,
            reason_code=reason_code, reason_text=reason_text,
            effective_date=effective_date, date_of_death=date_of_death,
            was_representative=was_representative,
            successor_group_individual_id=successor_id,
            propagation_id=propagation_id,
            is_person_level=is_person_level)
        record.save(user=self.user)

        if successor_id:
            self._promote_successor(successor_id, membership)
        GroupIndividual.objects.filter(pk=membership.pk).update(is_active=False)
        return record

    def _restore_membership(self, record):
        from individual.models import GroupIndividual

        membership = GroupIndividual.objects.filter(id=record.group_individual_id).first()
        if membership:
            GroupIndividual.objects.filter(pk=membership.pk).update(
                is_active=True, is_deleted=False)
        record.reactivated_at = timezone.now()
        record.reactivated_by = self.user
        record.save(user=self.user)

    @staticmethod
    def _has_successor_candidate(membership):
        from individual.models import GroupIndividual
        from case_management.models import MemberDeactivation

        return GroupIndividual.objects.filter(
            group_id=membership.group_id, is_deleted=False, is_active=True,
        ).exclude(id=membership.id).exists()

    def _promote_successor(self, successor_id, outgoing):
        from individual.models import GroupIndividual
        from individual.services import GroupAndGroupIndividualAlignmentService

        successor = GroupIndividual.objects.filter(id=successor_id, is_deleted=False).first()
        v.require(successor, v.CM_NOT_FOUND, 'Successor not found')
        v.require(successor.group_id == outgoing.group_id, v.CM_SUCCESSOR_REQUIRED,
                  'The successor must belong to the same household')
        alignment = GroupAndGroupIndividualAlignmentService(self.user)
        if outgoing.role == GroupIndividual.Role.HEAD:
            alignment.handle_head_change(successor.id, GroupIndividual.Role.HEAD,
                                         successor.group_id)
        if outgoing.recipient_type == GroupIndividual.RecipientType.PRIMARY:
            alignment.handle_primary_recipient_change(
                successor.id, GroupIndividual.RecipientType.PRIMARY, successor.group_id)

    @staticmethod
    def _is_representative(membership):
        from individual.models import GroupIndividual
        return (membership.role == GroupIndividual.Role.HEAD
                or membership.recipient_type == GroupIndividual.RecipientType.PRIMARY)

    def _set_beneficiary_status(self, beneficiary, status):
        """Status is owned by social_protection; go through its service so its signal fires.

        benefit_plan_id must be sent even when only status changes: upstream's
        would_exceed_max_active_beneficiaries() calls BenefitPlan.objects.get(id=...) before
        testing the status, so omitting it raises DoesNotExist.
        """
        from social_protection.services import BeneficiaryService, GroupBeneficiaryService
        from social_protection.models import GroupBeneficiary

        service_cls = (GroupBeneficiaryService if isinstance(beneficiary, GroupBeneficiary)
                       else BeneficiaryService)
        result = service_cls(self.user).update({
            'id': str(beneficiary.id),
            'status': status,
            'benefit_plan_id': str(beneficiary.benefit_plan_id),
        })
        if not result.get('success'):
            raise v.CaseValidationError('CM_STATUS_UPDATE_FAILED', result.get('detail'))

    @staticmethod
    def _require_no_payment_in_flight(group):
        from tasaf_payment.models import PaylistItem, PaylistItemStatus

        in_flight = PaylistItem.objects.filter(
            payment_account__group_beneficiary__group=group, is_deleted=False,
            status__in=[PaylistItemStatus.PENDING, PaylistItemStatus.PROCESSED],
        ).exists()
        v.require(not in_flight, v.CM_PAYMENT_IN_FLIGHT,
                  'A payment is in flight for this household')


class FollowUpService:

    def __init__(self, user):
        self.user = user

    @check_authentication
    @register_service_signal('case_followup_service.add')
    @_service_result
    def add(self, group_id, category, remark, priority=None, assigned_to_id=None,
            assigned_role_id=None, due_date=None, payment_change_audit_id=None,
            paylist_item_id=None, ticket_id=None, parent_remark_id=None):
        from individual.models import Group

        group = Group.objects.filter(id=group_id, is_deleted=False).first()
        v.require(group, v.CM_NOT_FOUND, 'Household not found')
        v.require(remark and len(remark.strip()) >= 5, v.CM_REASON_TEXT_REQUIRED,
                  'Write at least a few words')
        links = [payment_change_audit_id, paylist_item_id, ticket_id]
        v.require(len([x for x in links if x]) <= 1, v.CM_NO_CHANGE,
                  'Link the remark to at most one source')

        if due_date is None:
            due_date = date.today() + timedelta(days=CaseManagementConfig.followup_default_sla_days)

        record = FollowUpRemark(
            group=group, category=category, remark=remark.strip(),
            priority=priority or 'NORMAL', assigned_to_id=assigned_to_id,
            assigned_role_id=assigned_role_id, due_date=due_date,
            payment_change_audit_id=payment_change_audit_id,
            paylist_item_id=paylist_item_id, ticket_id=ticket_id,
            parent_remark_id=parent_remark_id)
        record.save(user=self.user)
        return output_result_success({'id': str(record.id)})

    @check_authentication
    @register_service_signal('case_followup_service.update_status')
    @_service_result
    def update_status(self, remark_id, status, resolution_note=None, version=None):
        record = FollowUpRemark.objects.filter(id=remark_id, is_deleted=False).first()
        v.require(record, v.CM_NOT_FOUND, 'Remark not found')
        v.validate_version(record, version)
        v.require(status != record.status, v.CM_NO_CHANGE, 'Nothing was changed')

        if status == FollowUpStatus.RESOLVED:
            v.require(resolution_note and resolution_note.strip(), v.CM_REASON_TEXT_REQUIRED,
                      'Describe how it was resolved')
            record.resolved_at = timezone.now()
            record.resolution_note = resolution_note.strip()
        if status == FollowUpStatus.ESCALATED:
            record.escalated_at = timezone.now()
        record.status = status
        record.save(user=self.user)
        return output_result_success({'id': str(record.id), 'status': status})

    @check_authentication
    @_service_result
    def assign(self, remark_id, assigned_to_id=None, assigned_role_id=None, version=None):
        record = FollowUpRemark.objects.filter(id=remark_id, is_deleted=False).first()
        v.require(record, v.CM_NOT_FOUND, 'Remark not found')
        v.validate_version(record, version)
        record.assigned_to_id = assigned_to_id
        record.assigned_role_id = assigned_role_id
        record.save(user=self.user)
        return output_result_success({'id': str(record.id)})

    @staticmethod
    def overdue_queryset():
        from case_management.models import OPEN_FOLLOW_UP_STATUSES
        return FollowUpRemark.objects.filter(
            status__in=OPEN_FOLLOW_UP_STATUSES, resolved_at__isnull=True,
            is_deleted=False, due_date__lt=date.today())


class PendingUpdateService:

    def __init__(self, user):
        self.user = user

    def open(self, target, update_type, group=None, severity=Severity.INFO,
             summary=None, task=None, approval_request=None):
        """Open a pending row, superseding any existing one for the same target."""
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(target.__class__)
        object_id = str(target.pk)
        existing = PendingDataUpdate.objects.filter(
            content_type=content_type, object_id=object_id,
            status=PendingStatus.PENDING, is_deleted=False).first()
        if existing:
            existing.status = PendingStatus.SUPERSEDED
            existing.resolved_at = timezone.now()
            existing.save(user=self.user)

        record = PendingDataUpdate(
            content_type=content_type, object_id=object_id,
            group=group, location=(group.location if group else None),
            update_type=update_type, status=PendingStatus.PENDING,
            severity=severity, task=task, approval_request=approval_request,
            summary=summary or {}, submitted_by=self.user)
        record.save(user=self.user)
        return record

    @check_authentication
    @register_service_signal('case_pending_service.decide')
    @_service_result
    def decide(self, pending_id, approve, note=None):
        record = PendingDataUpdate.objects.filter(
            id=pending_id, status=PendingStatus.PENDING, is_deleted=False).first()
        v.require(record, v.CM_NOT_FOUND, 'No pending update with that id')
        v.require(record.submitted_by_id != getattr(self.user, 'id', None), v.CM_SELF_APPROVAL,
                  'An update cannot be approved by the person who submitted it')

        with transaction.atomic():
            record.status = PendingStatus.APPROVED if approve else PendingStatus.REJECTED
            record.resolved_at = timezone.now()
            record.summary = {**(record.summary or {}), 'decision_note': note}
            record.save(user=self.user)
            if not approve:
                self._restore_rejected(record)
        return output_result_success({'id': str(record.id), 'status': record.status})

    def _restore_rejected(self, record):
        """A rejected payment change puts the account's verification statuses back."""
        from tasaf_payment.models import PaymentAccount

        if record.update_type != UpdateType.PAYMENT_CHANGE:
            return
        audit = (PaymentChangeAudit.objects
                 .filter(payment_account_id=record.object_id, is_material=True)
                 .order_by('-date_created').first())
        if not audit or audit.previous_verification_status is None:
            return
        account = PaymentAccount.objects.filter(id=record.object_id).first()
        if not account:
            return
        for field, delta in (audit.changed_fields or {}).items():
            setattr(account, field, delta.get('before'))
        account.verification_status = audit.previous_verification_status
        if audit.previous_pre_audit_status:
            account.pre_audit_status = audit.previous_pre_audit_status
        account.save(user=self.user)

    def close_for_target(self, target, status=PendingStatus.APPROVED):
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(target.__class__)
        for record in PendingDataUpdate.objects.filter(
                content_type=content_type, object_id=str(target.pk),
                status=PendingStatus.PENDING, is_deleted=False):
            record.status = status
            record.resolved_at = timezone.now()
            record.save(user=self.user)


