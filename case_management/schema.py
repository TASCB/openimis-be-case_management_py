"""Federated GraphQL schema — openIMIS discovers ``Query`` / ``Mutation``."""
import graphene
import graphene_django_optimizer as gql_optimizer
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.translation import gettext as _

from core.schema import OrderedDjangoFilterConnectionField

from case_management.apps import CaseManagementConfig
from case_management.models import _scope_to_user
from case_management.gql_mutations import (
    AddPaymentAccountMutation,
    ProposeMemberAddMutation,
    AddFollowUpRemarkMutation, DeactivateHouseholdMutation, DeactivateMemberMutation,
    DecidePendingUpdateMutation, ReactivateHouseholdMutation, ReactivateMemberMutation,
    ReversePropagationMutation,
    UpdateFollowUpRemarkMutation, UpdateHouseholdRepresentativeMutation,
    UpdatePaymentDetailsMutation, UpdatePaymentPhoneMutation,
)
from case_management.gql_queries import (
    CaseChannelCountGQLType, CaseManagementSummaryGQLType, CaseStatusCountGQLType,
    PaymentAccountCorrectionGQLType,
    CaseFollowUpRemarkGQLType,
    CaseHouseholdDeactivationGQLType, CaseMemberDeactivationGQLType,
    CasePaymentChangeAuditGQLType, CasePendingDataUpdateGQLType, CasePaymentProviderGQLType,
    CaseChangeOutcomeGQLType,
)
from case_management.models import (
    FollowUpRemark, FollowUpStatus, HouseholdDeactivation, MemberDeactivation,
    OPEN_FOLLOW_UP_STATUSES, PaymentChangeAudit, PendingDataUpdate, PendingStatus,
)


def _check(user, perms):
    if not user or user.is_anonymous or not user.id or not user.has_perms(perms):
        raise PermissionDenied(_("unauthorized"))


ACCOUNT_LOCATION = 'group_beneficiary__group__location'


class Query(graphene.ObjectType):
    account_correction = OrderedDjangoFilterConnectionField(
        PaymentAccountCorrectionGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        fsp_code=graphene.String(required=False),
        location_id=graphene.Int(required=False),
        hhid=graphene.String(required=False),
        recipient_name=graphene.String(required=False),
        reason=graphene.String(required=False),
        description="Payment accounts to correct: reason VERIFICATION_FAILED (default) or INVALID_MOBILE.",
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
    case_payment_providers = graphene.List(CasePaymentProviderGQLType)
    case_change_outcomes = graphene.List(
        CaseChangeOutcomeGQLType, client_mutation_ids=graphene.List(graphene.String, required=True))
    case_payment_account = graphene.Field(PaymentAccountCorrectionGQLType, group_id=graphene.UUID(required=True))

    case_pending_data_update = OrderedDjangoFilterConnectionField(
        CasePendingDataUpdateGQLType, orderBy=graphene.List(of_type=graphene.String))

    def resolve_case_payment_account(self, info, group_id):
        user = info.context.user
        perms = (CaseManagementConfig.gql_payment_change_search_perms
                 + CaseManagementConfig.gql_payment_change_update_perms
                 + CaseManagementConfig.gql_payment_phone_update_perms)
        if not (user and user.id and any(user.has_perms([p]) for p in perms)):
            raise PermissionDenied(_("unauthorized"))
        from tasaf_payment.models import PaymentAccount
        qs = PaymentAccount.objects.filter(is_deleted=False, group_beneficiary__group_id=group_id)
        return (_scope_to_user(qs, user, ACCOUNT_LOCATION)
                .order_by('-is_primary', '-date_created').first())

    def resolve_case_change_outcomes(self, info, client_mutation_ids):
        from core.models import MutationLog
        from case_management.services import CLIENT_MUTATION_ID

        user = info.context.user
        if not user or user.is_anonymous or not user.id:
            raise PermissionDenied(_("unauthorized"))
        ids = list(dict.fromkeys(i for i in client_mutation_ids if i))[:200]
        logs = {}
        for log in MutationLog.objects.filter(client_mutation_id__in=ids, user_id=user.id).order_by('request_date_time'):
            logs[log.client_mutation_id] = log
        rows = {}
        for row in (PendingDataUpdate.objects.filter(is_deleted=False, submitted_by_id=user.id,
                                                     **{f'json_ext__{CLIENT_MUTATION_ID}__in': ids})
                    .order_by('date_created')):
            rows[row.json_ext[CLIENT_MUTATION_ID]] = row
        outcomes = []
        for cid in ids:
            log, row = logs.get(cid), rows.get(cid)
            if log is None and row is None:
                continue
            summary = (row.summary or {}) if row else {}
            outcomes.append(CaseChangeOutcomeGQLType(
                client_mutation_id=cid,
                mutation_status=log.status if log else None,
                mutation_error=log.error if log else None,
                pending_id=str(row.id) if row else None,
                status=row.status if row else None,
                decision_note=summary.get('decision_note') or summary.get('apply_error'),
                resolved_at=row.resolved_at if row else None,
                applied=(row.status == PendingStatus.APPROVED and not summary.get('apply_failed'))
                if row and row.status != PendingStatus.PENDING else None,
            ))
        return outcomes

    def resolve_case_payment_providers(self, info, **kwargs):
        user = info.context.user
        if not (user and user.id and (
                user.has_perms(CaseManagementConfig.gql_payment_change_update_perms)
                or user.has_perms(CaseManagementConfig.gql_payment_phone_update_perms))):
            raise PermissionDenied(_("unauthorized"))
        from tasaf_payment.apps import TasafPaymentConfig
        from tasaf_payment.muse_setup import provider_list
        prefixes = TasafPaymentConfig.mobile_prefixes or {}
        return [
            CasePaymentProviderGQLType(
                name=row['name'], fsp_code=row['fsp_code'], fsp_type=row['fsp_type'], names=row['names'],
                prefixes=prefixes.get(row['fsp_code'], []) if row['fsp_type'] == 'MOBILE' else [])
            for row in provider_list() if row['fsp_type'] in ('BANK', 'MOBILE')
        ]

    def resolve_case_management_summary(self, info, **kwargs):
        from datetime import date

        from django.db.models import Count

        from tasaf_payment.models import PaymentAccount, VerificationStatus

        user = info.context.user
        _check(user, CaseManagementConfig.gql_case_search_perms)

        live = {'is_deleted': False}
        follow_ups = FollowUpRemark.get_queryset(FollowUpRemark.objects.filter(**live), user)
        pending = PendingDataUpdate.get_queryset(PendingDataUpdate.objects.filter(**live), user)

        def by_status(qs):
            rows = qs.values('status').order_by('status').annotate(c=Count('id'))
            return [CaseStatusCountGQLType(status=r['status'], count=r['c']) for r in rows]

        channel_rows = (pending.filter(status=PendingStatus.PENDING)
                        .values('channel').order_by('channel').annotate(c=Count('id')))

        return CaseManagementSummaryGQLType(
            open_corrections=_scope_to_user(PaymentAccount.objects.filter(
                is_deleted=False, verification_status=VerificationStatus.FAILED), user, ACCOUNT_LOCATION).count(),
            open_follow_ups=follow_ups.filter(status__in=OPEN_FOLLOW_UP_STATUSES).count(),
            overdue_follow_ups=follow_ups.filter(
                status__in=OPEN_FOLLOW_UP_STATUSES, due_date__lt=date.today()).count(),
            pending_updates=pending.filter(status=PendingStatus.PENDING).count(),
            households_deactivated=HouseholdDeactivation.get_queryset(
                HouseholdDeactivation.objects.filter(**live), user).count(),
            members_deactivated=MemberDeactivation.get_queryset(
                MemberDeactivation.objects.filter(**live), user).count(),
            payment_changes=PaymentChangeAudit.get_queryset(
                PaymentChangeAudit.objects.filter(**live), user).count(),
            follow_ups_by_status=by_status(follow_ups),
            pending_by_status=by_status(pending),
            pending_by_channel=[CaseChannelCountGQLType(channel=r['channel'], count=r['c'])
                                for r in channel_rows],
        )

    def resolve_account_correction(self, info, **kwargs):
        """Accounts to correct: failed verification (default), or mobile-money accounts whose
        number is not 255 + 9 digits. Corrected accounts leave the list on their own."""
        _check(info.context.user, CaseManagementConfig.gql_account_correction_search_perms)
        from tasaf_payment.models import PaymentAccount, VerificationStatus
        from tasaf_payment.msisdn import MSISDN_DB_PATTERN

        if kwargs.get('reason') == 'INVALID_MOBILE':
            qs = PaymentAccount.objects.filter(is_deleted=False, fsp_type='MOBILE').exclude(
                account_number__regex=MSISDN_DB_PATTERN)
        else:
            qs = PaymentAccount.objects.filter(
                is_deleted=False, verification_status=VerificationStatus.FAILED)
        qs = _scope_to_user(qs.select_related('group_beneficiary__group'), info.context.user,
                            ACCOUNT_LOCATION)

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
    add_payment_account = AddPaymentAccountMutation.Field()
    propose_member_add = ProposeMemberAddMutation.Field()
    deactivate_household = DeactivateHouseholdMutation.Field()
    reactivate_household = ReactivateHouseholdMutation.Field()
    deactivate_member = DeactivateMemberMutation.Field()
    reactivate_member = ReactivateMemberMutation.Field()
    reverse_propagation = ReversePropagationMutation.Field()
    update_household_representative = UpdateHouseholdRepresentativeMutation.Field()
    add_follow_up_remark = AddFollowUpRemarkMutation.Field()
    update_follow_up_remark = UpdateFollowUpRemarkMutation.Field()
    decide_pending_update = DecidePendingUpdateMutation.Field()
