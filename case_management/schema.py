"""Federated GraphQL schema — openIMIS discovers ``Query`` / ``Mutation``."""
import graphene
import graphene_django_optimizer as gql_optimizer
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.translation import gettext as _

from core.schema import OrderedDjangoFilterConnectionField

from case_management.apps import CaseManagementConfig
from case_management.gql_mutations import (
    AddFollowUpRemarkMutation, DeactivateHouseholdMutation, DeactivateMemberMutation,
    DecidePendingUpdateMutation, ReactivateHouseholdMutation, ReactivateMemberMutation,
    ReversePropagationMutation,
    UpdateFollowUpRemarkMutation, UpdateHouseholdRepresentativeMutation,
    UpdatePaymentDetailsMutation, UpdatePaymentPhoneMutation,
)
from case_management.gql_queries import (
    CaseManagementSummaryGQLType, CaseStatusCountGQLType,
    PaymentAccountCorrectionGQLType,
    CaseFollowUpRemarkGQLType,
    CaseHouseholdDeactivationGQLType, CaseMemberDeactivationGQLType,
    CasePaymentChangeAuditGQLType, CasePendingDataUpdateGQLType,
)
from case_management.models import (
    FollowUpRemark, FollowUpStatus, HouseholdDeactivation, MemberDeactivation,
    OPEN_FOLLOW_UP_STATUSES, PaymentChangeAudit, PendingDataUpdate, PendingStatus,
)


def _check(user, perms):
    if not user or user.is_anonymous or not user.id or not user.has_perms(perms):
        raise PermissionDenied(_("unauthorized"))


class Query(graphene.ObjectType):
    account_correction = OrderedDjangoFilterConnectionField(
        PaymentAccountCorrectionGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        fsp_code=graphene.String(required=False),
        location_id=graphene.Int(required=False),
        hhid=graphene.String(required=False),
        recipient_name=graphene.String(required=False),
        description="Payment accounts that failed verification and need correcting.",
    )

    case_payment_change_audit = OrderedDjangoFilterConnectionField(
        CasePaymentChangeAuditGQLType, orderBy=graphene.List(of_type=graphene.String))
    case_household_deactivation = OrderedDjangoFilterConnectionField(
        CaseHouseholdDeactivationGQLType, orderBy=graphene.List(of_type=graphene.String))
    case_member_deactivation = OrderedDjangoFilterConnectionField(
        CaseMemberDeactivationGQLType, orderBy=graphene.List(of_type=graphene.String))
    case_follow_up_remark = OrderedDjangoFilterConnectionField(
        CaseFollowUpRemarkGQLType, orderBy=graphene.List(of_type=graphene.String),
        overdue_only=graphene.Boolean())
    case_management_summary = graphene.Field(CaseManagementSummaryGQLType)

    case_pending_data_update = OrderedDjangoFilterConnectionField(
        CasePendingDataUpdateGQLType, orderBy=graphene.List(of_type=graphene.String))

    def resolve_case_management_summary(self, info, **kwargs):
        from datetime import date

        from django.db.models import Count

        from tasaf_payment.models import PaymentAccount, VerificationStatus

        _check(info.context.user, CaseManagementConfig.gql_case_search_perms)

        live = {'is_deleted': False}
        follow_ups = FollowUpRemark.objects.filter(**live)
        pending = PendingDataUpdate.objects.filter(**live)

        def by_status(qs):
            rows = qs.values('status').order_by('status').annotate(c=Count('id'))
            return [CaseStatusCountGQLType(status=r['status'], count=r['c']) for r in rows]

        return CaseManagementSummaryGQLType(
            open_corrections=PaymentAccount.objects.filter(
                is_deleted=False, verification_status=VerificationStatus.FAILED).count(),
            open_follow_ups=follow_ups.filter(status__in=OPEN_FOLLOW_UP_STATUSES).count(),
            overdue_follow_ups=follow_ups.filter(
                status__in=OPEN_FOLLOW_UP_STATUSES, due_date__lt=date.today()).count(),
            pending_updates=pending.filter(status=PendingStatus.PENDING).count(),
            households_deactivated=HouseholdDeactivation.objects.filter(**live).count(),
            members_deactivated=MemberDeactivation.objects.filter(**live).count(),
            payment_changes=PaymentChangeAudit.objects.filter(**live).count(),
            follow_ups_by_status=by_status(follow_ups),
            pending_by_status=by_status(pending),
        )

    def resolve_account_correction(self, info, **kwargs):
        """Failed accounts, newest failure first. Corrected accounts leave this list on
        their own: CasePaymentService resets them to PENDING for the next bulk run."""
        _check(info.context.user, CaseManagementConfig.gql_account_correction_search_perms)
        from tasaf_payment.models import PaymentAccount, VerificationStatus

        qs = PaymentAccount.objects.filter(
            is_deleted=False, verification_status=VerificationStatus.FAILED,
        ).select_related('group_beneficiary__group')

        if kwargs.get('location_id'):
            qs = qs.filter(group_beneficiary__group__location_id=kwargs['location_id'])
        if kwargs.get('fsp_code'):
            from tasaf_payment.reports import fsp_names_for_code
            qs = qs.filter(fsp_name__in=fsp_names_for_code(kwargs['fsp_code']))
        if kwargs.get('hhid'):
            qs = qs.filter(group_beneficiary__group__code__icontains=kwargs['hhid'])
        if kwargs.get('recipient_name'):
            term = kwargs['recipient_name']
            qs = qs.filter(
                group_beneficiary__group__groupindividuals__is_deleted=False,
                group_beneficiary__group__groupindividuals__recipient_type='PRIMARY',
            ).filter(
                Q(group_beneficiary__group__groupindividuals__individual__first_name__icontains=term)
                | Q(group_beneficiary__group__groupindividuals__individual__last_name__icontains=term)
            ).distinct()

        return gql_optimizer.query(qs, info)

    def resolve_case_payment_change_audit(self, info, **kwargs):
        _check(info.context.user, CaseManagementConfig.gql_payment_change_search_perms)
        return gql_optimizer.query(
            PaymentChangeAudit.objects.filter(is_deleted=False).order_by('-date_created'), info)

    def resolve_case_household_deactivation(self, info, **kwargs):
        _check(info.context.user, CaseManagementConfig.gql_deactivation_search_perms)
        return gql_optimizer.query(
            HouseholdDeactivation.objects.filter(is_deleted=False).order_by('-date_created'), info)

    def resolve_case_member_deactivation(self, info, **kwargs):
        _check(info.context.user, CaseManagementConfig.gql_deactivation_search_perms)
        return gql_optimizer.query(
            MemberDeactivation.objects.filter(is_deleted=False).order_by('-date_created'), info)

    def resolve_case_follow_up_remark(self, info, **kwargs):
        from datetime import date
        _check(info.context.user, CaseManagementConfig.gql_followup_search_perms)
        qs = FollowUpRemark.objects.filter(is_deleted=False).order_by('-date_created')
        if kwargs.get('overdue_only'):
            qs = qs.filter(status__in=OPEN_FOLLOW_UP_STATUSES,
                           resolved_at__isnull=True, due_date__lt=date.today())
        return gql_optimizer.query(qs, info)

    def resolve_case_pending_data_update(self, info, **kwargs):
        _check(info.context.user, CaseManagementConfig.gql_pending_update_search_perms)
        return gql_optimizer.query(
            PendingDataUpdate.objects.filter(is_deleted=False).order_by('-date_created'), info)


class Mutation(graphene.ObjectType):
    update_payment_details = UpdatePaymentDetailsMutation.Field()
    update_payment_phone = UpdatePaymentPhoneMutation.Field()
    deactivate_household = DeactivateHouseholdMutation.Field()
    reactivate_household = ReactivateHouseholdMutation.Field()
    deactivate_member = DeactivateMemberMutation.Field()
    reactivate_member = ReactivateMemberMutation.Field()
    reverse_propagation = ReversePropagationMutation.Field()
    update_household_representative = UpdateHouseholdRepresentativeMutation.Field()
    add_follow_up_remark = AddFollowUpRemarkMutation.Field()
    update_follow_up_remark = UpdateFollowUpRemarkMutation.Field()
    decide_pending_update = DecidePendingUpdateMutation.Field()
