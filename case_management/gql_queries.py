"""GraphQL object types. Names are prefixed ``Case*`` — graphene type names are global."""
import graphene
from graphene_django import DjangoObjectType

from core import ExtendedConnection

from tasaf_payment.models import PaymentAccount
from case_management.models import (
    FollowUpRemark, HouseholdDeactivation, MemberDeactivation, PaymentChangeAudit,
    PendingDataUpdate,
)


class CasePaymentChangeAuditGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = PaymentChangeAudit
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "payment_account_id": ["exact"],
            "group_beneficiary_id": ["exact"],
            "group_beneficiary__group__id": ["exact"],
            "change_type": ["exact", "in"],
            "is_material": ["exact"],
            "channel": ["exact", "in"],
            "reason_code": ["exact", "in"],
            "date_created": ["exact", "gt", "gte", "lt", "lte"],
            "is_deleted": ["exact"],
        }
        connection_class = ExtendedConnection

    @classmethod
    def get_queryset(cls, queryset, info):
        return PaymentChangeAudit.get_queryset(queryset, info.context.user)


class CaseHouseholdDeactivationGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    is_open = graphene.Boolean()

    class Meta:
        model = HouseholdDeactivation
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "group_id": ["exact"],
            "group__id": ["exact"],
            "mode": ["exact", "in"],
            "reason_code": ["exact", "in"],
            "effective_date": ["exact", "gt", "gte", "lt", "lte"],
            "reactivated_at": ["isnull"],
            "is_deleted": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_is_open(self, info):
        return self.reactivated_at is None

    @classmethod
    def get_queryset(cls, queryset, info):
        return HouseholdDeactivation.get_queryset(queryset, info.context.user)


class CaseMemberDeactivationGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    is_open = graphene.Boolean()

    class Meta:
        model = MemberDeactivation
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "group_id": ["exact"],
            "group__id": ["exact"],
            "individual_id": ["exact"],
            "group_individual_id": ["exact"],
            "reason_code": ["exact", "in"],
            "was_representative": ["exact"],
            "is_person_level": ["exact"],
            "propagation_id": ["exact", "isnull"],
            "effective_date": ["exact", "gt", "gte", "lt", "lte"],
            "reactivated_at": ["isnull"],
            "is_deleted": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_is_open(self, info):
        return self.reactivated_at is None

    @classmethod
    def get_queryset(cls, queryset, info):
        return MemberDeactivation.get_queryset(queryset, info.context.user)


class CaseFollowUpRemarkGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    is_overdue = graphene.Boolean()

    class Meta:
        model = FollowUpRemark
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "group_id": ["exact"],
            "group__id": ["exact"],
            "category": ["exact", "in"],
            "status": ["exact", "in"],
            "priority": ["exact", "in"],
            "assigned_to_id": ["exact"],
            "assigned_role_id": ["exact"],
            "due_date": ["exact", "gt", "gte", "lt", "lte", "isnull"],
            "resolved_at": ["isnull"],
            "parent_remark_id": ["exact", "isnull"],
            "is_deleted": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_is_overdue(self, info):
        from datetime import date
        return bool(self.due_date and not self.resolved_at and self.due_date < date.today())

    @classmethod
    def get_queryset(cls, queryset, info):
        return FollowUpRemark.get_queryset(queryset, info.context.user)


class CasePendingDataUpdateGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = PendingDataUpdate
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "group_id": ["exact"],
            "group__id": ["exact"],
            "location_id": ["exact"],
            "object_id": ["exact"],
            "update_type": ["exact", "in"],
            "status": ["exact", "in"],
            "severity": ["exact", "in"],
            "submitted_by_id": ["exact"],
            "date_created": ["exact", "gt", "gte", "lt", "lte"],
            "is_deleted": ["exact"],
        }
        connection_class = ExtendedConnection

    @classmethod
    def get_queryset(cls, queryset, info):
        return PendingDataUpdate.get_queryset(queryset, info.context.user)


class VerificationAttemptGQLType(graphene.ObjectType):
    """One MUSE verification round-trip, for the correction drill-in's history panel."""
    muse_reference = graphene.String()
    verification_type = graphene.String()
    result = graphene.String()
    failure_reason = graphene.String()
    received_at = graphene.DateTime()


class PaymentAccountCorrectionGQLType(DjangoObjectType):
    """A failed account, with the context a field team needs to act on it.

    The name is the household representative's, resolved the same way the verification
    batch does -- never the head (they differ in ~37% of households) and never
    account_name, which historically holds the HHID because the questionnaire did not
    collect account details.
    """
    uuid = graphene.String(source='uuid')
    hhid = graphene.String()
    recipient_name = graphene.String()
    failure_reason = graphene.String()
    location_name = graphene.String()
    verification_attempts = graphene.List(VerificationAttemptGQLType)

    class Meta:
        model = PaymentAccount
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            'id': ['exact'],
            'account_number': ['exact', 'icontains'],
            'fsp_name': ['exact', 'icontains'],
            'fsp_type': ['exact'],
        }
        connection_class = ExtendedConnection

    def resolve_hhid(root, info):
        group = getattr(getattr(root, 'group_beneficiary', None), 'group', None)
        return getattr(group, 'code', None)

    def resolve_recipient_name(root, info):
        from tasaf_payment.services import MuseVerificationDispatchService
        return MuseVerificationDispatchService.recipient_name(root)

    def resolve_failure_reason(root, info):
        record = (root.muse_verification_records
                  .exclude(failure_reason__isnull=True).exclude(failure_reason='')
                  .order_by('-date_created').first())
        return record.failure_reason if record else None

    def resolve_verification_attempts(root, info):
        return list(root.muse_verification_records.order_by('-received_at')[:10])

    def resolve_location_name(root, info):
        group = getattr(getattr(root, 'group_beneficiary', None), 'group', None)
        location = getattr(group, 'location', None)
        return getattr(location, 'name', None)
