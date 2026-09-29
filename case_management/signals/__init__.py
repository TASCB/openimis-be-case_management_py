"""Cross-module service-signal bindings.

Consumers bind to signals the owning modules already emit; nothing upstream is modified.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def _payload(kwargs):
    data = kwargs.get('data') or []
    args = data[0] if len(data) > 0 else ()
    kwds = data[1] if len(data) > 1 else {}
    return args, (kwds or {}), kwargs.get('result')


def on_payment_account_updated(**kwargs):
    """Audit a payment account changed outside PaymentChangeService."""
    from case_management.models import Channel, PaymentChangeAudit
    from case_management.services import TRACKED_PAYMENT_FIELDS, classify, in_case_management

    if in_case_management():
        return
    try:
        args, kwds, result = _payload(kwargs)
        if not isinstance(result, dict) or not result.get('success'):
            return
        obj_data = args[0] if args else kwds.get('obj_data')
        if not isinstance(obj_data, dict) or not obj_data.get('id'):
            return

        from tasaf_payment.models import PaymentAccount
        account = PaymentAccount.objects.filter(id=obj_data['id']).first()
        if not account:
            return
        history = list(account.history.order_by('-history_date')[:2])
        if len(history) < 2:
            return
        before = {f: getattr(history[1], f, None) for f in TRACKED_PAYMENT_FIELDS}
        after = {f: getattr(history[0], f, None) for f in TRACKED_PAYMENT_FIELDS}
        change_type, is_material, changed = classify(before, after)
        if not changed:
            return

        audit = PaymentChangeAudit(
            payment_account=account,
            group_beneficiary_id=account.group_beneficiary_id,
            change_type=change_type, is_material=is_material,
            changed_fields=changed, channel=Channel.SYSTEM,
            reason_code='OUT_OF_BAND',
            reason_text='Recorded by signal; the change did not come through case management.')
        audit.save(user=getattr(kwargs.get('cls_'), 'user', None) or account.user_updated)
    except Exception:
        logger.exception("case_management: payment account audit consumer failed")


def on_approval_finalized(**kwargs):
    """Close the pending update when the approval engine finalises our request."""
    from case_management.models import PendingDataUpdate, PendingStatus
    from case_management.services import PendingUpdateService

    try:
        args, kwds, result = _payload(kwargs)
        approval_request = args[0] if args else kwds.get('approval_request')
        decision = args[1] if len(args) > 1 else kwds.get('decision')
        if approval_request is None:
            return
        pending = PendingDataUpdate.objects.filter(
            approval_request_id=approval_request.id,
            status=PendingStatus.PENDING, is_deleted=False).first()
        if not pending:
            return
        approved = str(decision).upper().endswith('APPROVED')
        actor = getattr(kwargs.get('cls_'), 'user', None) or pending.submitted_by
        service = PendingUpdateService(actor)
        pending.status = PendingStatus.APPROVED if approved else PendingStatus.REJECTED
        pending.resolved_at = timezone.now()
        pending.save(user=actor)
        if not approved:
            service._restore_rejected(pending)
        service.invalidate_banner()
    except Exception:
        logger.exception("case_management: approval finalized consumer failed")


def on_deactivation_task_complete(**kwargs):
    """Apply a deactivation once a checker completes its task.

    The task carries the original payload; it is replayed against the service with `_approved=True`
    so the maker-checker guard does not queue it a second time. A declined task applies nothing.
    """
    from datetime import date

    try:
        from core.models import User
        from tasks_management.models import Task
        from case_management.services import (
            DEACTIVATE_HOUSEHOLD_EVENT, DEACTIVATE_MEMBER_EVENT, HouseholdCaseService,
        )

        result = kwargs.get('result') or {}
        if not result.get('success'):
            return
        task = result['data']['task']
        event = task.get('business_event') or ''
        if event not in (DEACTIVATE_MEMBER_EVENT, DEACTIVATE_HOUSEHOLD_EVENT):
            return
        if task.get('status') == Task.Status.FAILED:
            logger.info('case_management: deactivation task %s declined', task.get('id'))
            return

        data = dict(task.get('data') or {})
        if data.get('effective_date'):
            data['effective_date'] = date.fromisoformat(data['effective_date'])
        if data.get('date_of_death'):
            data['date_of_death'] = date.fromisoformat(data['date_of_death'])

        user = User.objects.get(id=result['data']['user']['id'])
        service = HouseholdCaseService(user)
        if event == DEACTIVATE_MEMBER_EVENT:
            service.deactivate_member(_approved=True, **data)
        else:
            service.deactivate_household(_approved=True, **data)
    except Exception:
        logger.exception('case_management: deactivation task handler failed')


def on_payment_change_task_complete(**kwargs):
    """Apply a proposed payment change once a checker completes its task."""
    try:
        from core.models import User
        from tasks_management.models import Task
        from case_management.services import PAYMENT_CHANGE_EVENT, PaymentChangeService

        result = kwargs.get('result') or {}
        if not result.get('success'):
            return
        task = result['data']['task']
        if (task.get('business_event') or '') != PAYMENT_CHANGE_EVENT:
            return
        from case_management.services import DataUpdateService

        user = User.objects.get(id=result['data']['user']['id'])
        task_obj = Task.objects.filter(id=task.get('id')).first()
        if task.get('status') == Task.Status.FAILED:
            logger.info('case_management: payment change task %s declined', task.get('id'))
        else:
            data = dict(task.get('data') or {})
            PaymentChangeService(user).update_details(
                payment_account_id=data['payment_account_id'],
                fields=data.get('fields') or {},
                reason_code=data.get('reason_code'),
                reason_text=data.get('reason_text'),
                channel=data.get('channel'),
                _approved=True,
            )
        if task_obj is not None:
            DataUpdateService(user).close_for_task(task_obj)
    except Exception:
        logger.exception('case_management: payment change task handler failed')


def _actor(kwargs):
    return getattr(kwargs.get('cls_'), 'user', None)


def on_data_update_task_created(**kwargs):
    """A household/member edit was proposed: index it in Pending updates."""
    try:
        from tasks_management.models import Task
        from case_management.services import WATCHED_TASK_EVENTS, DataUpdateService

        result = kwargs.get('result') or {}
        if not result.get('success'):
            return
        task = Task.objects.filter(id=(result.get('data') or {}).get('id')).first()
        if task is None or task.business_event not in WATCHED_TASK_EVENTS:
            return
        DataUpdateService(_actor(kwargs) or task.user_created).propose(task)
    except Exception:
        logger.exception('case_management: data update proposal consumer failed')


def _direct_update_target(signal_name, kwargs):
    """The record an Update* mutation is saving without a task (its maker-checker flag is off).

    Only while the matching mutation runs, which excludes the task executor applying an approved
    edit, alignment side effects, imports and case management's own writes."""
    from case_management import mutation_context
    from case_management.services import DIRECT_UPDATE_MUTATIONS

    ctx = mutation_context.current()
    if not ctx or ctx.get('mutation_class') != DIRECT_UPDATE_MUTATIONS[signal_name]:
        return None, None
    args, kwds, _ = _payload(kwargs)
    obj_data = args[0] if args else kwds.get('obj_data')
    model = getattr(kwargs.get('cls_'), 'OBJECT_TYPE', None)
    if not isinstance(obj_data, dict) or model is None:
        return ctx, None
    return ctx, model.objects.filter(id=obj_data.get('id')).first()


def _on_direct_update(signal_name):
    def before(**kwargs):
        try:
            from case_management.services import snapshot

            ctx, target = _direct_update_target(signal_name, kwargs)
            if target is not None:
                ctx.setdefault('before', {})[str(target.pk)] = snapshot(target)
        except Exception:
            logger.exception('case_management: direct data update snapshot failed (%s)', signal_name)

    def after(**kwargs):
        try:
            from case_management.services import DataUpdateService

            result = kwargs.get('result')
            if not isinstance(result, dict) or not result.get('success'):
                return
            ctx, target = _direct_update_target(signal_name, kwargs)
            if target is None:
                return
            before = (ctx.get('before') or {}).pop(str(target.pk), None)
            if before is not None:
                DataUpdateService(_actor(kwargs)).record_applied(target, before)
        except Exception:
            logger.exception('case_management: direct data update consumer failed (%s)', signal_name)

    return before, after


def on_data_update_task_complete(**kwargs):
    """Close the pending row when its task is completed, from Pending updates or the Tasks inbox.

    Bound by the signal_binding crawler after individual's executors, so an approved change has
    already been applied (or has failed) by the time this runs."""
    try:
        from tasks_management.models import Task
        from case_management.services import WATCHED_TASK_EVENTS, DataUpdateService

        result = kwargs.get('result') or {}
        if not result.get('success'):
            return
        task_data = result['data']['task']
        if (task_data.get('business_event') or '') not in WATCHED_TASK_EVENTS:
            return
        task = Task.objects.filter(id=task_data.get('id')).first()
        if task is not None:
            DataUpdateService(_actor(kwargs)).close_for_task(task)
    except Exception:
        logger.exception('case_management: data update task completion consumer failed')


def bind_service_signals():
    from core.service_signals import ServiceSignalBindType
    from core.signals import bind_service_signal

    bindings = (
        ('payment_account_service.update', on_payment_account_updated),
        ('approval_service.finalized', on_approval_finalized),
        ('task_service.complete_task', on_deactivation_task_complete),
        ('task_service.complete_task', on_payment_change_task_complete),
        ('task_service.create', on_data_update_task_created),
        ('task_service.complete_task', on_data_update_task_complete),
    ) + tuple((name, after) for name, (_, after) in _DIRECT_UPDATE_HANDLERS.items())
    for name, (before, _) in _DIRECT_UPDATE_HANDLERS.items():
        try:
            bind_service_signal(name, before, bind_type=ServiceSignalBindType.BEFORE)
        except Exception:
            logger.warning('case_management: could not bind %s', name, exc_info=True)
    for name, handler in bindings:
        try:
            bind_service_signal(name, handler, bind_type=ServiceSignalBindType.AFTER)
        except Exception:
            logger.warning('case_management: could not bind %s', name, exc_info=True)


_DIRECT_UPDATE_HANDLERS = {
    name: _on_direct_update(name)
    for name in ('individual_service.update', 'groupindividual_service.update', 'group_service.update')
}
