import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='case_management.sweep_overdue_follow_ups')
def sweep_overdue_follow_ups(self):
    """Notify assignees of follow-ups past their due date. Idempotent per day via group_key."""
    from case_management.apps import CaseManagementConfig
    from case_management.services import FollowUpService
    from notifications.services import NotificationService

    if not CaseManagementConfig.enable_followup_sla_sweep:
        return {'skipped': 'flag off'}

    service = NotificationService()
    sent = 0
    try:
        for remark in FollowUpService.overdue_queryset().select_related('assigned_to', 'group'):
            if not remark.assigned_to_id:
                continue
            sent += service.notify(
                'case.followup.overdue',
                subject=f'Follow-up overdue: {remark.get_category_display()}',
                body=remark.remark[:280],
                target_route=f'/caseManagement/followUps/{remark.id}',
                source_ref=str(remark.id),
                group_key=f'case.followup.overdue:{remark.id}',
                subject_user=remark.assigned_to,
            )
    except Exception as exc:
        raise self.retry(exc=exc)
    return {'notified': sent}
