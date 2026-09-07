"""GraphQL mutations. Thin wrappers: rights here, business rules in the services."""
import json

import graphene
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from core.gql.gql_mutations.base_mutation import BaseMutation
from core.schema import OpenIMISMutation

from case_management.apps import CaseManagementConfig
from case_management.services import (
    FollowUpService, HouseholdCaseService, PaymentChangeService, PendingUpdateService,
)


def _strip_client(data):
    data.pop('client_mutation_id', None)
    data.pop('client_mutation_label', None)


def _require(user, perms):
    if not user or user.is_anonymous or not user.id or not user.has_perms(perms):
        raise PermissionDenied(_("unauthorized"))


def _failure(result):
    """openIMIS expects a list of {message, detail}. The machine-readable code and any payload
    travel as JSON in ``detail`` — that string is the mobile client's error contract."""
    return [{
        'message': result.get('message') or 'Case management mutation failed',
        'detail': json.dumps({
            'code': result.get('code') or 'CM_ERROR',
            'detail': result.get('detail'),
            'payload': result.get('payload') or {},
        }),
    }]


def _run(service_call):
    result = service_call()
    return None if result.get('success') else _failure(result)


class UpdatePaymentDetailsMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "UpdatePaymentDetailsMutation"

    class Input(OpenIMISMutation.Input):
        payment_account_id = graphene.UUID(required=True)
        version = graphene.Int(required=False)
        account_number = graphene.String(required=False)
        account_name = graphene.String(required=False)
        fsp_type = graphene.String(required=False)
        fsp_name = graphene.String(required=False)
        is_primary = graphene.Boolean(required=False)
        contact_phone = graphene.String(required=False)
        reason_code = graphene.String(required=False)
        reason_text = graphene.String(required=False)
        channel = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_payment_change_update_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        editable = ('account_number', 'account_name', 'fsp_type', 'fsp_name',
                    'is_primary', 'contact_phone')
        fields = {k: data[k] for k in editable if k in data and data[k] is not None}
        return _run(lambda: PaymentChangeService(user).update_details(
            payment_account_id=data['payment_account_id'],
            fields=fields,
            reason_code=data.get('reason_code'),
            reason_text=data.get('reason_text'),
            version=data.get('version'),
            channel=data.get('channel')))


class UpdatePaymentPhoneMutation(BaseMutation):
    """Separate mutation and separate right, deliberately: one method with an optional reason
    would in practice be called without one."""
    _mutation_module = "case_management"
    _mutation_class = "UpdatePaymentPhoneMutation"

    class Input(OpenIMISMutation.Input):
        payment_account_id = graphene.UUID(required=True)
        contact_phone = graphene.String(required=True)
        version = graphene.Int(required=False)
        channel = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_payment_phone_update_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: PaymentChangeService(user).update_phone(
            payment_account_id=data['payment_account_id'],
            contact_phone=data['contact_phone'],
            version=data.get('version'),
            channel=data.get('channel')))


class DeactivateHouseholdMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "DeactivateHouseholdMutation"

    class Input(OpenIMISMutation.Input):
        group_id = graphene.UUID(required=True)
        mode = graphene.String(required=True)
        reason_code = graphene.String(required=True)
        reason_text = graphene.String(required=False)
        effective_date = graphene.Date(required=True)
        version = graphene.Int(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_deactivation_create_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: HouseholdCaseService(user).deactivate_household(
            group_id=data['group_id'], mode=data['mode'],
            reason_code=data['reason_code'], reason_text=data.get('reason_text'),
            effective_date=data['effective_date'], version=data.get('version')))


class ReactivateHouseholdMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "ReactivateHouseholdMutation"

    class Input(OpenIMISMutation.Input):
        group_id = graphene.UUID(required=True)
        reason_text = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_reactivation_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: HouseholdCaseService(user).reactivate_household(
            group_id=data['group_id'], reason_text=data.get('reason_text')))


class SuccessorInput(graphene.InputObjectType):
    group_id = graphene.UUID(required=True)
    group_individual_id = graphene.UUID(required=True)


class DeactivateMemberMutation(BaseMutation):
    """``successors`` is a list because a deceased person may head several households."""
    _mutation_module = "case_management"
    _mutation_class = "DeactivateMemberMutation"

    class Input(OpenIMISMutation.Input):
        group_individual_id = graphene.UUID(required=True)
        reason_code = graphene.String(required=True)
        reason_text = graphene.String(required=False)
        effective_date = graphene.Date(required=True)
        date_of_death = graphene.Date(required=False)
        successors = graphene.List(SuccessorInput, required=False)
        person_level = graphene.Boolean(required=False)
        version = graphene.Int(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_deactivation_create_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        successors = {str(s['group_id']): str(s['group_individual_id'])
                      for s in (data.get('successors') or [])}
        return _run(lambda: HouseholdCaseService(user).deactivate_member(
            group_individual_id=data['group_individual_id'],
            reason_code=data['reason_code'], reason_text=data.get('reason_text'),
            effective_date=data['effective_date'],
            date_of_death=data.get('date_of_death'),
            successors=successors, person_level=data.get('person_level') or False,
            version=data.get('version')))


class ReactivateMemberMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "ReactivateMemberMutation"

    class Input(OpenIMISMutation.Input):
        group_individual_id = graphene.UUID(required=True)
        reason_text = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_reactivation_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: HouseholdCaseService(user).reactivate_member(
            group_individual_id=data['group_individual_id'],
            reason_text=data.get('reason_text')))


class ReversePropagationMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "ReversePropagationMutation"

    class Input(OpenIMISMutation.Input):
        propagation_id = graphene.UUID(required=True)
        reason_text = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_reactivation_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: HouseholdCaseService(user).reverse_propagation(
            propagation_id=data['propagation_id'], reason_text=data.get('reason_text')))


class UpdateHouseholdRepresentativeMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "UpdateHouseholdRepresentativeMutation"

    class Input(OpenIMISMutation.Input):
        group_individual_id = graphene.UUID(required=True)
        role = graphene.String(required=False)
        recipient_type = graphene.String(required=False)
        reason_text = graphene.String(required=False)
        version = graphene.Int(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_payment_change_update_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: HouseholdCaseService(user).update_representative(
            group_individual_id=data['group_individual_id'],
            role=data.get('role'), recipient_type=data.get('recipient_type'),
            reason_text=data.get('reason_text'), version=data.get('version')))


class AddFollowUpRemarkMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "AddFollowUpRemarkMutation"

    class Input(OpenIMISMutation.Input):
        group_id = graphene.UUID(required=True)
        category = graphene.String(required=True)
        remark = graphene.String(required=True)
        priority = graphene.String(required=False)
        assigned_to_id = graphene.Int(required=False)
        assigned_role_id = graphene.Int(required=False)
        due_date = graphene.Date(required=False)
        payment_change_audit_id = graphene.UUID(required=False)
        paylist_item_id = graphene.UUID(required=False)
        ticket_id = graphene.UUID(required=False)
        parent_remark_id = graphene.UUID(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_followup_create_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: FollowUpService(user).add(
            group_id=data['group_id'], category=data['category'], remark=data['remark'],
            priority=data.get('priority'), assigned_to_id=data.get('assigned_to_id'),
            assigned_role_id=data.get('assigned_role_id'), due_date=data.get('due_date'),
            payment_change_audit_id=data.get('payment_change_audit_id'),
            paylist_item_id=data.get('paylist_item_id'), ticket_id=data.get('ticket_id'),
            parent_remark_id=data.get('parent_remark_id')))


class UpdateFollowUpRemarkMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "UpdateFollowUpRemarkMutation"

    class Input(OpenIMISMutation.Input):
        remark_id = graphene.UUID(required=True)
        status = graphene.String(required=False)
        resolution_note = graphene.String(required=False)
        assigned_to_id = graphene.Int(required=False)
        assigned_role_id = graphene.Int(required=False)
        version = graphene.Int(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_followup_update_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        service = FollowUpService(user)
        if data.get('status'):
            return _run(lambda: service.update_status(
                remark_id=data['remark_id'], status=data['status'],
                resolution_note=data.get('resolution_note'), version=data.get('version')))
        return _run(lambda: service.assign(
            remark_id=data['remark_id'], assigned_to_id=data.get('assigned_to_id'),
            assigned_role_id=data.get('assigned_role_id'), version=data.get('version')))


class DecidePendingUpdateMutation(BaseMutation):
    _mutation_module = "case_management"
    _mutation_class = "DecidePendingUpdateMutation"

    class Input(OpenIMISMutation.Input):
        pending_id = graphene.UUID(required=True)
        approve = graphene.Boolean(required=True)
        note = graphene.String(required=False)

    @classmethod
    def _validate_mutation(cls, user, **data):
        _require(user, CaseManagementConfig.gql_pending_update_decide_perms)

    @classmethod
    def _mutate(cls, user, **data):
        _strip_client(data)
        return _run(lambda: PendingUpdateService(user).decide(
            pending_id=data['pending_id'], approve=data['approve'], note=data.get('note')))
