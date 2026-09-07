"""Case management models."""
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import HistoryModel, User
from location.models import Location, LocationManager


def _scope_to_user(queryset, user, prefix):
    """Restrict to the districts ``user`` may see. ``prefix`` is the path to a Location."""
    if user is None or user.is_anonymous:
        return queryset.none()
    if getattr(user, 'is_imis_admin', False):
        return queryset
    return queryset.filter(
        LocationManager().build_user_location_filter_query(user._u, prefix=prefix)
    )


class ChangeType(models.TextChoices):
    ACCOUNT_NUMBER = 'ACCOUNT_NUMBER', _('Account number')
    PROVIDER = 'PROVIDER', _('Provider / FSP')
    ACCOUNT_NAME = 'ACCOUNT_NAME', _('Account name')
    CONTACT_PHONE = 'CONTACT_PHONE', _('Contact phone')
    PRIMARY_FLAG = 'PRIMARY_FLAG', _('Primary account flag')
    MULTIPLE = 'MULTIPLE', _('Multiple fields')


class Channel(models.TextChoices):
    WEB = 'WEB', _('Web console')
    MOBILE = 'MOBILE', _('Mobile app')
    IMPORT = 'IMPORT', _('Import / ETL')
    SYSTEM = 'SYSTEM', _('System')


class DeactivationMode(models.TextChoices):
    TEMPORARY = 'TEMPORARY', _('Temporary')
    PERMANENT = 'PERMANENT', _('Permanent')


class FollowUpCategory(models.TextChoices):
    PAYMENT_FAILURE = 'PAYMENT_FAILURE', _('Payment failure')
    ACCOUNT_VERIFICATION = 'ACCOUNT_VERIFICATION', _('Account verification')
    DATA_QUALITY = 'DATA_QUALITY', _('Data quality')
    BENEFICIARY_CONTACT = 'BENEFICIARY_CONTACT', _('Beneficiary contact')
    OTHER = 'OTHER', _('Other')


class FollowUpStatus(models.TextChoices):
    OPEN = 'OPEN', _('Open')
    IN_PROGRESS = 'IN_PROGRESS', _('In progress')
    RESOLVED = 'RESOLVED', _('Resolved')
    ESCALATED = 'ESCALATED', _('Escalated')
    CANCELLED = 'CANCELLED', _('Cancelled')


OPEN_FOLLOW_UP_STATUSES = (
    FollowUpStatus.OPEN, FollowUpStatus.IN_PROGRESS, FollowUpStatus.ESCALATED,
)


class Priority(models.TextChoices):
    LOW = 'LOW', _('Low')
    NORMAL = 'NORMAL', _('Normal')
    HIGH = 'HIGH', _('High')
    URGENT = 'URGENT', _('Urgent')


class UpdateType(models.TextChoices):
    PAYMENT_CHANGE = 'PAYMENT_CHANGE', _('Payment change')
    HOUSEHOLD_UPDATE = 'HOUSEHOLD_UPDATE', _('Household update')
    MEMBER_UPDATE = 'MEMBER_UPDATE', _('Member update')
    DEACTIVATION = 'DEACTIVATION', _('Deactivation')
    REPRESENTATIVE_CHANGE = 'REPRESENTATIVE_CHANGE', _('Representative change')


class PendingStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending')
    APPROVED = 'APPROVED', _('Approved')
    REJECTED = 'REJECTED', _('Rejected')
    CANCELLED = 'CANCELLED', _('Cancelled')
    SUPERSEDED = 'SUPERSEDED', _('Superseded')


class Severity(models.TextChoices):
    INFO = 'INFO', _('Info')
    WARNING = 'WARNING', _('Warning')
    CRITICAL = 'CRITICAL', _('Critical')


class PaymentChangeAudit(HistoryModel):
    """Why a payment account changed. ``is_material`` drives reason, status reset and approval."""
    payment_account = models.ForeignKey(
        'tasaf_payment.PaymentAccount', models.DO_NOTHING,
        related_name='change_audits',
    )
    group_beneficiary = models.ForeignKey(
        'social_protection.GroupBeneficiary', models.DO_NOTHING,
        null=True, blank=True, related_name='payment_change_audits',
    )
    change_type = models.CharField(max_length=30, choices=ChangeType.choices)
    is_material = models.BooleanField(default=False)
    changed_fields = models.JSONField(default=dict, blank=True)
    reason_code = models.CharField(max_length=50, null=True, blank=True)
    reason_text = models.TextField(null=True, blank=True)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.WEB)
    approval_request = models.ForeignKey(
        'approval.ApprovalRequest', models.DO_NOTHING,
        null=True, blank=True, related_name='payment_change_audits',
    )
    previous_verification_status = models.IntegerField(null=True, blank=True)
    previous_pre_audit_status = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'case_PaymentChangeAudit'
        indexes = [
            models.Index(fields=['payment_account', '-date_created'],
                         name='case_pca_account_idx'),
            models.Index(fields=['group_beneficiary', '-date_created'],
                         name='case_pca_household_idx'),
            models.Index(fields=['is_material', '-date_created'],
                         condition=Q(is_material=True), name='case_pca_material_idx'),
            models.Index(fields=['channel', '-date_created'], name='case_pca_channel_idx'),
        ]

    def __str__(self):
        return f'{self.change_type} on {self.payment_account_id} ({self.channel})'

    @classmethod
    def get_queryset(cls, queryset, user):
        if queryset is None:
            queryset = cls.objects.all()
        return _scope_to_user(queryset, user, 'group_beneficiary__group__location')


class HouseholdDeactivation(HistoryModel):
    """A household leaving active service. Both modes set SUSPENDED; never GRADUATED."""
    group = models.ForeignKey(
        'individual.Group', models.DO_NOTHING, related_name='case_deactivations',
    )
    group_beneficiary = models.ForeignKey(
        'social_protection.GroupBeneficiary', models.DO_NOTHING,
        null=True, blank=True, related_name='case_deactivations',
    )
    mode = models.CharField(max_length=20, choices=DeactivationMode.choices)
    reason_code = models.CharField(max_length=50)
    reason_text = models.TextField(null=True, blank=True)
    effective_date = models.DateField()
    previous_status = models.CharField(max_length=100)
    reactivated_at = models.DateTimeField(null=True, blank=True)
    reactivated_by = models.ForeignKey(
        User, models.DO_NOTHING, null=True, blank=True,
        related_name='case_household_reactivations',
    )
    reactivation_reason = models.TextField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'case_HouseholdDeactivation'
        indexes = [
            models.Index(fields=['group', '-date_created'], name='case_hd_group_idx'),
            models.Index(fields=['group'],
                         condition=Q(reactivated_at__isnull=True, is_deleted=False),
                         name='case_hd_open_idx'),
            models.Index(fields=['mode', '-effective_date'], name='case_hd_mode_idx'),
        ]

    def __str__(self):
        return f'{self.group_id} {self.mode} ({self.reason_code})'

    @classmethod
    def get_queryset(cls, queryset, user):
        if queryset is None:
            queryset = cls.objects.all()
        return _scope_to_user(queryset, user, 'group__location')


class MemberDeactivation(HistoryModel):
    """A membership ending. ``DECEASED`` cascades across households under one propagation_id."""
    group_individual = models.ForeignKey(
        'individual.GroupIndividual', models.DO_NOTHING, related_name='case_deactivations',
    )
    individual = models.ForeignKey(
        'individual.Individual', models.DO_NOTHING, related_name='case_deactivations',
    )
    group = models.ForeignKey(
        'individual.Group', models.DO_NOTHING, related_name='case_member_deactivations',
    )
    reason_code = models.CharField(max_length=50)
    reason_text = models.TextField(null=True, blank=True)
    effective_date = models.DateField()
    date_of_death = models.DateField(null=True, blank=True)
    was_representative = models.BooleanField(default=False)
    successor_group_individual = models.ForeignKey(
        'individual.GroupIndividual', models.DO_NOTHING,
        null=True, blank=True, related_name='case_successor_of',
    )
    propagation_id = models.UUIDField(null=True, blank=True)
    is_person_level = models.BooleanField(default=False)
    reactivated_at = models.DateTimeField(null=True, blank=True)
    reactivated_by = models.ForeignKey(
        User, models.DO_NOTHING, null=True, blank=True,
        related_name='case_member_reactivations',
    )
    reactivation_reason = models.TextField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'case_MemberDeactivation'
        indexes = [
            models.Index(fields=['group_individual', '-date_created'], name='case_md_gi_idx'),
            models.Index(fields=['group', '-effective_date'], name='case_md_group_idx'),
            models.Index(fields=['individual', '-effective_date'], name='case_md_indiv_idx'),
            models.Index(fields=['propagation_id'],
                         condition=Q(propagation_id__isnull=False), name='case_md_prop_idx'),
            models.Index(fields=['group'],
                         condition=Q(reactivated_at__isnull=True, is_deleted=False),
                         name='case_md_open_idx'),
        ]

    def __str__(self):
        return f'{self.individual_id} out of {self.group_id} ({self.reason_code})'

    @classmethod
    def get_queryset(cls, queryset, user):
        if queryset is None:
            queryset = cls.objects.all()
        return _scope_to_user(queryset, user, 'group__location')


class FollowUpRemark(HistoryModel):
    """Tracked casework against a household."""
    group = models.ForeignKey(
        'individual.Group', models.DO_NOTHING, related_name='case_follow_ups',
    )
    payment_change_audit = models.ForeignKey(
        PaymentChangeAudit, models.DO_NOTHING,
        null=True, blank=True, related_name='follow_ups',
    )
    paylist_item = models.ForeignKey(
        'tasaf_payment.PaylistItem', models.DO_NOTHING,
        null=True, blank=True, related_name='case_follow_ups',
    )
    ticket = models.ForeignKey(
        'grievance_social_protection.Ticket', models.DO_NOTHING,
        null=True, blank=True, related_name='case_follow_ups',
    )
    category = models.CharField(max_length=40, choices=FollowUpCategory.choices)
    remark = models.TextField()
    status = models.CharField(
        max_length=20, choices=FollowUpStatus.choices, default=FollowUpStatus.OPEN,
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL,
    )
    assigned_to = models.ForeignKey(
        User, models.DO_NOTHING, null=True, blank=True, related_name='case_follow_ups',
    )
    assigned_role_id = models.IntegerField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    parent_remark = models.ForeignKey(
        'self', models.DO_NOTHING, null=True, blank=True, related_name='replies',
    )

    class Meta:
        managed = True
        db_table = 'case_FollowUpRemark'
        indexes = [
            models.Index(fields=['group', '-date_created'], name='case_fr_group_idx'),
            models.Index(fields=['status', 'due_date'],
                         condition=Q(status__in=list(OPEN_FOLLOW_UP_STATUSES)),
                         name='case_fr_open_idx'),
            models.Index(fields=['assigned_to', 'status'], name='case_fr_assignee_idx'),
            models.Index(fields=['assigned_role_id', 'status'], name='case_fr_role_idx'),
            models.Index(fields=['due_date'],
                         condition=Q(resolved_at__isnull=True, is_deleted=False),
                         name='case_fr_overdue_idx'),
        ]

    def __str__(self):
        return f'{self.category} {self.status} ({self.group_id})'

    @classmethod
    def get_queryset(cls, queryset, user):
        if queryset is None:
            queryset = cls.objects.all()
        return _scope_to_user(queryset, user, 'group__location')


class PendingDataUpdate(HistoryModel):
    """Index over work awaiting a decision. Pointer + status; the source task is authoritative."""
    content_type = models.ForeignKey(ContentType, models.DO_NOTHING)
    object_id = models.CharField(max_length=255)
    target = GenericForeignKey('content_type', 'object_id')

    group = models.ForeignKey(
        'individual.Group', models.DO_NOTHING,
        null=True, blank=True, related_name='case_pending_updates',
    )
    location = models.ForeignKey(
        Location, models.DO_NOTHING,
        null=True, blank=True, related_name='case_pending_updates',
    )
    update_type = models.CharField(max_length=40, choices=UpdateType.choices)
    status = models.CharField(
        max_length=20, choices=PendingStatus.choices, default=PendingStatus.PENDING,
    )
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.INFO,
    )
    task = models.ForeignKey(
        'tasks_management.Task', models.DO_NOTHING,
        null=True, blank=True, related_name='case_pending_updates',
    )
    approval_request = models.ForeignKey(
        'approval.ApprovalRequest', models.DO_NOTHING,
        null=True, blank=True, related_name='case_pending_updates',
    )
    summary = models.JSONField(default=dict, blank=True)
    submitted_by = models.ForeignKey(
        User, models.DO_NOTHING, related_name='case_submitted_updates',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'case_PendingDataUpdate'
        indexes = [
            models.Index(fields=['status', 'location', 'severity'],
                         condition=Q(status=PendingStatus.PENDING), name='case_pdu_banner_idx'),
            models.Index(fields=['content_type', 'object_id'], name='case_pdu_target_idx'),
            models.Index(fields=['group', 'status'], name='case_pdu_group_idx'),
            models.Index(fields=['task'], name='case_pdu_task_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['content_type', 'object_id'],
                condition=Q(status=PendingStatus.PENDING, is_deleted=False),
                name='case_pdu_one_pending_per_target',
            ),
        ]

    def __str__(self):
        return f'{self.update_type} {self.status} ({self.object_id})'

    @classmethod
    def get_queryset(cls, queryset, user):
        if queryset is None:
            queryset = cls.objects.all()
        return _scope_to_user(queryset, user, 'location')
