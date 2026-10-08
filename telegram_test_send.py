"""One manually confirmed test message. No retry, schedule, or token persistence."""
import http.client
import json
import os
import re
import ssl
import sys

EXPECTED_BOT='daniel45_etf_bot'
TEST_TEXT='ETF 알림 연결 테스트'
MAX_RESPONSE=128*1024

class TestSendError(Exception): pass
class DeliveryUncertain(Exception): pass


def request(token, method, payload):
    if method not in {'getMe','getChat','sendMessage'}:
        raise TestSendError('Unsupported operation')
    connection=None
    try:
        connection=http.client.HTTPSConnection('api.telegram.org',timeout=12,context=ssl.create_default_context())
        connection.request('POST','/bot'+token+'/'+method,body=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        response=connection.getresponse()
        raw=response.read(MAX_RESPONSE+1)
        if response.status>=500 or len(raw)>MAX_RESPONSE:
            raise ValueError('Unexpected response')
        data=json.loads(raw)
        if not isinstance(data,dict) or type(data.get('ok')) is not bool:
            raise ValueError('Invalid response')
        if data['ok'] is False:
            raise TestSendError('Telegram rejected the request; no automatic retry')
        if response.status!=200:
            raise ValueError('Unexpected response')
        return data.get('result')
    except TestSendError:
        raise
    except Exception:
        if method=='sendMessage':
            raise DeliveryUncertain('Delivery outcome is unknown. Check your Telegram chat; do not rerun automatically') from None
        raise TestSendError('Recipient checks failed; no message sent') from None
    finally:
        if connection: connection.close()


def send_once(token,chat_id,confirmation,call=request):
    if confirmation!='SEND_ONE_TEST':
        raise TestSendError('Explicit SEND_ONE_TEST confirmation is required')
    if not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,100}',token or ''):
        raise TestSendError('Bot secret missing or invalid')
    if not re.fullmatch(r'[1-9]\d{0,15}',chat_id or ''):
        raise TestSendError('Private recipient secret missing or invalid')
    cid=int(chat_id)
    me=call(token,'getMe',{})
    if not isinstance(me,dict) or me.get('is_bot') is not True or str(me.get('username','')).lower()!=EXPECTED_BOT:
        raise TestSendError('Unexpected bot identity; no message sent')
    chat=call(token,'getChat',{'chat_id':cid})
    if not isinstance(chat,dict) or chat.get('type')!='private' or type(chat.get('id')) is not int or chat['id']!=cid:
        raise TestSendError('Recipient is not the configured private chat; no message sent')
    # Exactly one transmission per manual invocation, without any retry loop.
    result=call(token,'sendMessage',{'chat_id':cid,'text':TEST_TEXT,'link_preview_options':{'is_disabled':True},'allow_paid_broadcast':False})
    if not isinstance(result,dict) or type(result.get('message_id')) is not int or result['message_id']<=0 or result.get('chat',{}).get('id')!=cid:
        raise DeliveryUncertain('Telegram acceptance could not be verified. Check your chat before any further action')
    return True


def main():
    try:
        if os.getenv('GITHUB_EVENT_NAME')!='workflow_dispatch' or os.getenv('GITHUB_REF')!='refs/heads/main':
            raise TestSendError('Manual main-branch execution only')
        if os.getenv('GITHUB_RUN_ATTEMPT')!='1':
            raise TestSendError('Reruns are disabled for this one-time test; check Telegram before further action')
        send_once(os.getenv('TELEGRAM_BOT_TOKEN',''),os.getenv('TELEGRAM_CHAT_ID',''),os.getenv('CONFIRMATION',''))
        print('Telegram accepted one connection-test message for the verified private recipient. Reading/delivery to the device is not confirmed.')
        return 0
    except DeliveryUncertain as error:
        print(str(error));return 2
    except TestSendError as error:
        print(str(error));return 1
    except BaseException:
        print('Execution stopped without diagnostic details. If submission started, check Telegram before retrying.');return 2

if __name__=='__main__':sys.exit(main())
