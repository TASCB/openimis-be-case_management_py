"""The channel of the GraphQL mutation being executed on this thread.

Clients declare it in `mutationExtensions: {"channel": "MOBILE"}`; it is read from core's
`signal_mutation`, sent before every mutation.
"""
import contextvars
import json
import logging

from case_management.models import Channel

logger = logging.getLogger(__name__)

_CTX = contextvars.ContextVar('case_mutation_ctx', default=None)


def current():
    return _CTX.get()


def current_channel():
    ctx = _CTX.get()
    return ctx['channel'] if ctx else None


def channel_from_extensions(extensions):
    if isinstance(extensions, str):
        try:
            extensions = json.loads(extensions)
        except ValueError:
            return None
    if not isinstance(extensions, dict) or extensions.get('channel') in (None, ''):
        return None
    value = str(extensions['channel']).strip().upper()
    if value not in Channel.values:
        logger.warning('case_management: unknown mutation channel %r ignored', value)
        return None
    return Channel(value)


def on_mutation(sender, **kwargs):
    # core treats a receiver's return value as a list of errors: never return None, never raise.
    try:
        data = kwargs.get('data') or {}
        _CTX.set({
            'mutation_class': kwargs.get('mutation_class'),
            'mutation_log_id': kwargs.get('mutation_log_id'),
            'channel': channel_from_extensions(data.get('mutation_extensions')),
        })
    except Exception:
        _CTX.set(None)
        logger.exception('case_management: could not record the mutation context')
    return []


def clear(**kwargs):
    _CTX.set(None)


def connect():
    from django.core.signals import request_finished
    from core.schema import signal_mutation

    signal_mutation.connect(on_mutation, dispatch_uid='case_management.mutation_context')
    request_finished.connect(clear, dispatch_uid='case_management.mutation_context.clear')
