"""Proposed vs applied pending updates, and route household-edit tasks to the same task group
as member edits.

`is_proposal` defaults to False (a default-True `is_applied` could never be saved False: core's
pre_save resets falsy fields to their default). GroupService tasks join the task group that
already takes IndividualService tasks; if there is none, nothing is created.
"""
from django.db import migrations, models

SOURCE = 'GroupService'
MARK = 'case_management_added'


def route_group_tasks(apps, schema_editor):
    TaskGroup = apps.get_model('tasks_management', 'TaskGroup')
    live = TaskGroup.objects.filter(is_deleted=False)
    if any(SOURCE in ((g.json_ext or {}).get('task_sources') or []) for g in live):
        return
    for group in live:
        json_ext = group.json_ext or {}
        sources = json_ext.get('task_sources') or []
        if 'IndividualService' in sources:
            json_ext = {**json_ext, 'task_sources': sources + [SOURCE],
                        MARK: (json_ext.get(MARK) or []) + [SOURCE]}
            TaskGroup.objects.filter(id=group.id).update(json_ext=json_ext)
            return
    print(f'\n  case_management: no task group receives IndividualService tasks; {SOURCE} not routed')


def unroute_group_tasks(apps, schema_editor):
    TaskGroup = apps.get_model('tasks_management', 'TaskGroup')
    for group in TaskGroup.objects.filter(is_deleted=False):
        json_ext = group.json_ext or {}
        if SOURCE not in (json_ext.get(MARK) or []):
            continue
        json_ext = {**json_ext,
                    'task_sources': [s for s in json_ext.get('task_sources') or [] if s != SOURCE],
                    MARK: [s for s in json_ext[MARK] if s != SOURCE]}
        if not json_ext[MARK]:
            del json_ext[MARK]
        TaskGroup.objects.filter(id=group.id).update(json_ext=json_ext)


class Migration(migrations.Migration):

    dependencies = [
        ('case_management', '0009_pending_update_channel'),
        ('tasks_management', '0010_add_search_all_perms_admin'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalpendingdataupdate',
            name='is_proposal',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='pendingdataupdate',
            name='is_proposal',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(route_group_tasks, unroute_group_tasks),
    ]
