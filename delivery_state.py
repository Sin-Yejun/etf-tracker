"""Pure durable-ledger transitions. No transport, credentials or remote writes.

Caller must atomically persist the returned ledger before submitting to Telegram.
GitHub compare-and-swap persistence is future work; these functions alone do not
make a GitHub runner's local filesystem durable.
"""
from copy import deepcopy


def claim(ledger, key, now):
    result = deepcopy(ledger)
    row = result.get(key)
    if row and not (row['state'] == 'retry_wait' and row['not_before'] <= now):
        return result, False
    result[key] = {'state': 'sending', 'claimed_at': now}
    return result, True


def complete(ledger, key, now, *, accepted_message_id=None, retry_after=None,
             rejected=False):
    if ledger.get(key, {}).get('state') != 'sending':
        raise ValueError('Only a persisted sending claim may complete')
    result = deepcopy(ledger)
    if accepted_message_id is not None:
        if not isinstance(accepted_message_id, int) or isinstance(accepted_message_id, bool) or accepted_message_id <= 0:
            raise ValueError('Invalid accepted message ID')
        result[key] = {'state': 'sent', 'accepted_at': now, 'message_id': accepted_message_id}
    elif retry_after is not None:
        if not isinstance(retry_after, int) or isinstance(retry_after, bool) or retry_after < 0:
            raise ValueError('Invalid retry-after')
        result[key] = {'state': 'retry_wait', 'not_before': now + max(1, retry_after)}
    elif rejected:
        result[key] = {'state': 'blocked', 'at': now}
    else:
        result[key] = {'state': 'uncertain', 'at': now}
    return result


def recover_interrupted(ledger):
    result = deepcopy(ledger)
    for row in result.values():
        if row['state'] == 'sending':
            row['state'] = 'uncertain'
    return result
