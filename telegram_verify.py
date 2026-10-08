"""Manual recipient verification only. Never sends messages or consumes updates."""
import http.client
import json
import os
import re
import ssl
import sys
import time

EXPECTED_BOT = 'daniel45_etf_bot'
ALLOWED = {'getMe', 'getWebhookInfo', 'getUpdates'}
MAX_RESPONSE = 1024 * 1024

class VerificationError(Exception):
    pass


def api(token, method, payload):
    if method not in ALLOWED:
        raise VerificationError('Unsupported verification operation')
    connection = None
    try:
        connection = http.client.HTTPSConnection('api.telegram.org', timeout=12, context=ssl.create_default_context())
        # Telegram requires the token in this TLS request path. Never print the
        # path, response, request, exception, or connection debug output.
        connection.request('POST', '/bot' + token + '/' + method,
                           body=json.dumps(payload).encode(),
                           headers={'Content-Type':'application/json'})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE:
            raise VerificationError('Telegram verification request failed; no settings changed')
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise VerificationError('Telegram did not confirm the verification request')
        return result.get('result')
    except Exception:
        # Do not attach original exceptions: they can include the token URL or data.
        raise VerificationError('Telegram verification request failed; check secret and retry manually') from None
    finally:
        if connection:
            connection.close()


def verify(token, phrase, call=api, now=None):
    if not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,100}', token or ''):
        raise VerificationError('TELEGRAM_BOT_TOKEN is missing or invalid; do not paste it in logs or chat')
    if not re.fullmatch(r'ETF-CHECK-[a-f0-9]{24}', phrase or ''):
        raise VerificationError('Use the exact fresh ETF-CHECK verification phrase')
    timestamp = int(time.time() if now is None else now)
    me = call(token, 'getMe', {})
    if not isinstance(me, dict) or me.get('is_bot') is not True or str(me.get('username','')).lower() != EXPECTED_BOT:
        raise VerificationError('The secret does not identify the expected bot; stopped')
    webhook = call(token, 'getWebhookInfo', {})
    if not isinstance(webhook, dict) or not isinstance(webhook.get('url'), str):
        raise VerificationError('Webhook state could not be verified; stopped')
    if webhook['url']:
        raise VerificationError('An existing webhook is configured; left unchanged and stopped')
    # No offset (positive or negative), allowed_updates, or acknowledgement call.
    # Only oldest 100 pending updates: never skip/consume to reach newer entries.
    updates = call(token, 'getUpdates', {'limit':100, 'timeout':0})
    if not isinstance(updates, list) or len(updates)>100:
        raise VerificationError('Unexpected update response; stopped')
    matches=set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        message=update.get('message')
        if not isinstance(message, dict) or message.get('text') != phrase:
            continue
        if any(k.startswith('forward_') for k in message) or any(k in message for k in ('is_automatic_forward','via_bot','sender_chat')):
            continue
        chat, sender=message.get('chat',{}),message.get('from',{})
        if not isinstance(chat,dict) or not isinstance(sender,dict):
            continue
        cid, uid, sent=chat.get('id'),sender.get('id'),message.get('date')
        if (chat.get('type')!='private' or sender.get('is_bot') is not False or
            type(cid) is not int or type(uid) is not int or cid!=uid or cid<=0 or
            type(sent) is not int or not timestamp-900<=sent<=timestamp+30):
            continue
        matches.add(cid)
    if len(matches)!=1:
        raise VerificationError('No unique fresh private-chat match; send a new phrase and retry. Pending backlog may hide it; no updates were consumed')
    return matches.pop()


def main():
    try:
        if os.getenv('GITHUB_EVENT_NAME')!='workflow_dispatch' or os.getenv('GITHUB_REF')!='refs/heads/main':
            raise VerificationError('Run manually from the main branch only')
        chat_id=verify(os.getenv('TELEGRAM_BOT_TOKEN',''),os.getenv('PAIRING_PHRASE',''))
        summary=os.getenv('GITHUB_STEP_SUMMARY')
        if not summary:
            raise VerificationError('Private job summary unavailable; no identifier output')
        with open(summary,'a',encoding='utf-8') as f:
            f.write('### Telegram private-chat verification\n\nExpected bot verified: @'+EXPECTED_BOT+'\n\nVerified personal TELEGRAM_CHAT_ID: `'+str(chat_id)+'`\n\nSave this value yourself in repository Actions Secrets as TELEGRAM_CHAT_ID. No message sent, no webhook changed, no update offset advanced.\n')
        print('Expected bot and one fresh private conversation verified. See private job summary for the chat ID. No message sent.')
        return 0
    except VerificationError as error:
        print('Verification stopped: '+str(error))
        return 1
    except BaseException:
        # No traceback for runtime failures, including data-dependent exceptions.
        print('Verification stopped safely; no diagnostic payload is printed')
        return 1

if __name__=='__main__':
    sys.exit(main())
