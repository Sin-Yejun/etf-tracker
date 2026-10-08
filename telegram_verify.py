"""Manual recipient verification only. Never sends messages or consumes updates."""
import http.client
import json
import os
import re
import ssl
import signal
import sys
import time

EXPECTED_BOT = 'daniel45_etf_bot'
ALLOWED = {'getMe', 'getWebhookInfo', 'getUpdates'}
MAX_RESPONSE = 1024 * 1024

class VerificationError(Exception):
    pass

class VerificationTimeout(BaseException):
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


def inspect_updates(updates, phrase, timestamp):
    if not isinstance(updates, list) or len(updates)>100:
        raise VerificationError('Unexpected update response; stopped')
    stats = dict(updates=len(updates), exact=0, private_exact=0, fresh_private=0,
                 edited_exact=0, forwarded_exact=0, eligible_chats=0)
    matches=set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        edited=update.get('edited_message')
        if isinstance(edited,dict) and edited.get('text')==phrase:
            stats['edited_exact']+=1
        message=update.get('message')
        if not isinstance(message, dict) or message.get('text') != phrase:
            continue
        stats['exact']+=1
        chat, sender=message.get('chat',{}),message.get('from',{})
        if not isinstance(chat,dict) or not isinstance(sender,dict):
            continue
        cid, uid, sent=chat.get('id'),sender.get('id'),message.get('date')
        private=chat.get('type')=='private'
        fresh=type(sent) is int and timestamp-900<=sent<=timestamp+30
        stats['private_exact']+=int(private)
        stats['fresh_private']+=int(private and fresh)
        forwarded=any(k.startswith('forward_') for k in message) or any(k in message for k in ('is_automatic_forward','via_bot','sender_chat'))
        stats['forwarded_exact']+=int(forwarded)
        if (forwarded or not private or not fresh or sender.get('is_bot') is not False or
            type(cid) is not int or type(uid) is not int or cid!=uid or cid<=0):
            continue
        matches.add(cid)
    stats['eligible_chats']=len(matches)
    return matches,stats


def failure_reason(stats):
    if stats['eligible_chats']>1:
        return 'AMBIGUOUS: multiple eligible private chats; stopped'
    if stats['updates']==0:
        return 'EMPTY_QUEUE: no pending updates returned; recipient remains unverified'
    if not stats['exact']:
        if stats['edited_exact']:
            return 'EDITED_ONLY: matching text appears only as an edit; a new direct message is required'
        if stats['updates']==100:
            return 'BATCH_FULL_NO_MATCH: no exact match in oldest 100; later updates may be outside this non-consuming view'
        return 'NO_EXACT_TEXT: returned incoming messages do not contain the exact verification phrase'
    if not stats['private_exact']:
        return 'NOT_PRIVATE: matching incoming text is not in a private chat'
    if not stats['fresh_private']:
        return 'OUTSIDE_TIME_WINDOW: matching private text is outside the unchanged 15-minute time window or has invalid timestamp'
    return 'IDENTITY_OR_FORWARD_FILTER: matching recent private text failed sender identity or direct-message checks'


def verify(token, phrase, call=api, now=None, attempts=1, sleep=time.sleep, diagnostic=None):
    if not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,100}', token or ''):
        raise VerificationError('TELEGRAM_BOT_TOKEN is missing or invalid; do not paste it in logs or chat')
    if not re.fullmatch(r'ETF-CHECK-[a-f0-9]{24}', phrase or ''):
        raise VerificationError('Use the exact fresh ETF-CHECK verification phrase')
    if type(attempts) is not int or not 1<=attempts<=7:
        raise VerificationError('Invalid bounded verification attempts')
    me=call(token,'getMe',{})
    if not isinstance(me,dict) or me.get('is_bot') is not True or str(me.get('username','')).lower()!=EXPECTED_BOT:
        raise VerificationError('The secret does not identify the expected bot; stopped')
    if diagnostic: diagnostic('Expected bot verified: @'+EXPECTED_BOT)
    for attempt in range(attempts):
        # Recheck before each poll; never alter or print webhook configuration.
        webhook=call(token,'getWebhookInfo',{})
        if not isinstance(webhook,dict) or not isinstance(webhook.get('url'),str):
            raise VerificationError('Webhook state could not be verified; stopped')
        if webhook['url']:
            raise VerificationError('An existing webhook is configured; left unchanged and stopped')
        if diagnostic:
            types=webhook.get('allowed_updates')
            message_subscription='unknown' if not isinstance(types,list) else 'included' if not types or 'message' in types else 'excluded'
            diagnostic('Webhook absent; message subscription: '+message_subscription)
        # No offset, allowed_updates, negative offset, acknowledgement or send.
        updates=call(token,'getUpdates',{'limit':100,'timeout':0})
        timestamp=int(time.time() if now is None else now)
        matches,stats=inspect_updates(updates,phrase,timestamp)
        if diagnostic:
            diagnostic('Verification sample '+str(attempt+1)+': '+json.dumps(stats,sort_keys=True))
        if len(matches)>1: raise VerificationError(failure_reason(stats))
        if len(matches)==1: return matches.pop()
        if attempt+1<attempts: sleep(8)
    raise VerificationError(failure_reason(stats)+'; no updates consumed or messages sent')


def main():
    try:
        if os.getenv('GITHUB_EVENT_NAME')!='workflow_dispatch' or os.getenv('GITHUB_REF')!='refs/heads/main':
            raise VerificationError('Run manually from the main branch only')
        def deadline(*_):
            raise VerificationTimeout()
        signal.signal(signal.SIGALRM,deadline)
        signal.setitimer(signal.ITIMER_REAL,60)
        chat_id=verify(os.getenv('TELEGRAM_BOT_TOKEN',''),os.getenv('PAIRING_PHRASE',''),attempts=7,diagnostic=print)
        signal.setitimer(signal.ITIMER_REAL,0)
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
    except VerificationTimeout:
        print('Verification stopped: 60-second limit reached; see last count-only sample. No updates consumed or messages sent')
        return 1
    except BaseException:
        # No traceback for runtime failures, including data-dependent exceptions.
        print('Verification stopped safely; no diagnostic payload is printed')
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)

if __name__=='__main__':
    sys.exit(main())
