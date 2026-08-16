# -*- coding: utf-8 -*-
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)
_logger.info("odoo_whatsapp_api: whatsapp_webhook_controller module loaded "
             "(routes /api/v1/whatsapp/webhook and /api/v1/whatsapp/webhook/<event> are active)")

# Events that message_process() ignores anyway (see the `else` branch in
# wa.webhook.messages.message_process, models/webhook.py) - there is
# therefore zero point paying for a full ORM create()/compute()/DB-cursor
# round trip to store and then discard them. Evolution/Baileys instances
# that are stuck in a reconnect loop can fire connection.update (and its
# siblings) hundreds of times per second, and each one used to open its
# own DB connection just to be thrown away - that's what was exhausting
# the connection pool ("PoolError: The Connection Pool Is Full") and
# taking the whole Odoo instance down with it. These are now dropped
# before ever touching the database.
_IGNORED_WA_EVENTS = {
    'connection.update',
    'connection-update',
    'qrcode.updated',
    'qrcode-updated',
    'presence.update',
    'presence-update',
    'contacts.set',
    'contacts-set',
    'contacts.upsert',
    'contacts-upsert',
    'chats.set',
    'chats-set',
    'chats.upsert',
    'chats-upsert',
    'chats.update',
    'chats-update',
    'groups.upsert',
    'groups-upsert',
    'groups.update',
    'groups-update',
    'application.startup',
    'application-startup',
}

# Belt-and-braces: even a *handled* event type can be re-fired abnormally
# fast by a misbehaving/looping Evolution instance. If the exact same
# (event, remote instance) combination is seen again within this window,
# drop it without touching the DB rather than opening another cursor.
_DEDUPE_WINDOW_SECONDS = 1.0
_last_seen = {}


def _is_flooding(dedupe_key):
    now = time.monotonic()
    last = _last_seen.get(dedupe_key)
    _last_seen[dedupe_key] = now
    # Simple unbounded-growth guard - this process-local dict is only ever
    # keyed by a handful of distinct instances/events in practice.
    if len(_last_seen) > 500:
        _last_seen.clear()
        _last_seen[dedupe_key] = now
        return False
    return last is not None and (now - last) < _DEDUPE_WINDOW_SECONDS


class WhatsAppWebhookController(http.Controller):
    """
    بيستقبل الـ POST requests اللي بتوصل من Evolution API (الـ Callback URL
    اللي اتسجل عن طريق action_subscribe_webhook أو اللي اتحط يدويًا في
    Evolution Manager > Events > Webhook).

    كل event (messages.upsert, messages.update, connection.update...) بيوصل
    كـ POST منفصل بالـ payload الخام. الأحداث اللي فعليًا بنعالجها
    (messages.upsert / messages.update) بس هي اللي بتتخزن في
    wa.webhook.messages وده بيشغّل message_process() تلقائيًا (compute
    field بيتفعّل وقت الـ create). أي حدث تاني (connection.update،
    qrcode.updated، ...) بيترفض من هنا على طول من غير ما يلمس الداتابيز
    خالص - كان قبل كده بيتعمل له create() كامل وبعدين يتجاهل جوه
    message_process()، وده اللي كان بيفضّي الـ connection pool لما
    Evolution يدخل في loop إعادة اتصال ويبعت مئات connection-update في
    الثانية.

    ملحوظة: Evolution API عندها إعداد اسمه "Webhook by Events" - لو مفعّل،
    بدل ما تبعت كل الأحداث على /api/v1/whatsapp/webhook، بتضيف اسم الحدث
    كـ sub-path (مثلاً /api/v1/whatsapp/webhook/messages-upsert). الـ
    method التانية (whatsapp_webhook_by_event) بتستقبل الشكل ده وتعالجه
    بنفس المنطق بالظبط.
    """

    def _store_webhook_payload(self, payload):
        request.env['wa.webhook.messages'].sudo().create({
            'json_content': json.dumps(payload),
        })

    def _should_drop(self, event_name, payload):
        """True لو الحدث ده ملهوش لازمة نخزنه (يا إما نوعه متجاهل أصلاً،
        يا إما نفس الحدث جالنا بمعدل غير طبيعي في أقل من ثانية) - في
        الحالتين منعمل ولا نلمس الداتابيز."""
        normalized = (event_name or '').lower().replace('_', '.').replace('-', '.')
        if normalized in {e.replace('-', '.') for e in _IGNORED_WA_EVENTS}:
            return True
        instance = payload.get('instance') or payload.get('sender') or ''
        if _is_flooding('%s:%s' % (normalized, instance)):
            _logger.debug(
                "Dropping WhatsApp webhook event %r (instance=%s): "
                "seen again within %.1fs, treating as flood/reconnect-loop noise",
                normalized, instance, _DEDUPE_WINDOW_SECONDS)
            return True
        return False

    @http.route('/api/v1/whatsapp/webhook', type='http', auth='public',
                methods=['POST'], csrf=False, cors="*")
    def whatsapp_webhook(self, **kwargs):
        try:
            raw_body = request.httprequest.get_data()
            payload = json.loads(raw_body) if raw_body else {}
        except (ValueError, TypeError):
            _logger.warning("Received invalid JSON on WhatsApp webhook")
            return request.make_response(
                json.dumps({'ok': False, 'error': 'invalid_json'}),
                headers=[('Content-Type', 'application/json')],
                status=400,
            )

        if self._should_drop(payload.get('event'), payload):
            # 200 دايمًا هنا، مش خطأ - Evolution لازم يفضل شايف إن الـ
            # webhook بيرد بنجاح، وإلا هيعتبره فشل ويعيد المحاولة، وده
            # هيزوّد الطوفان بدل ما يقلله.
            return request.make_response(
                json.dumps({'ok': True, 'ignored': True}),
                headers=[('Content-Type', 'application/json')],
                status=200,
            )

        try:
            self._store_webhook_payload(payload)
        except Exception:
            _logger.exception("Failed to store incoming WhatsApp webhook payload")
            return request.make_response(
                json.dumps({'ok': False, 'error': 'internal_error'}),
                headers=[('Content-Type', 'application/json')],
                status=500,
            )

        return request.make_response(
            json.dumps({'ok': True}),
            headers=[('Content-Type', 'application/json')],
            status=200,
        )

    @http.route('/api/v1/whatsapp/webhook', type='http', auth='public',
                methods=['GET'], csrf=False, cors="*")
    def whatsapp_webhook_verify(self, **kwargs):
        """Evolution API doesn't do a GET handshake like Meta's Cloud API,
        but some setups (or a browser sanity check) may hit this with GET -
        respond 200 instead of a 404/405 so it's obvious the route exists."""
        return request.make_response(
            json.dumps({'ok': True, 'message': 'WhatsApp webhook endpoint is up'}),
            headers=[('Content-Type', 'application/json')],
            status=200,
        )

    @http.route('/api/v1/whatsapp/webhook/<string:event_name>', type='http', auth='public',
                methods=['POST'], csrf=False, cors="*")
    def whatsapp_webhook_by_event(self, event_name, **kwargs):
        """نسخة من whatsapp_webhook() بس بتستقبل الشكل اللي فيه اسم الحدث
        جوه الـ URL (Evolution 'Webhook by Events')."""
        try:
            raw_body = request.httprequest.get_data()
            payload = json.loads(raw_body) if raw_body else {}
        except (ValueError, TypeError):
            _logger.warning("Received invalid JSON on WhatsApp webhook (event=%s)", event_name)
            return request.make_response(
                json.dumps({'ok': False, 'error': 'invalid_json'}),
                headers=[('Content-Type', 'application/json')],
                status=400,
            )

        if not payload.get('event'):
            payload['event'] = event_name.replace('-', '.')

        if self._should_drop(payload.get('event') or event_name, payload):
            return request.make_response(
                json.dumps({'ok': True, 'ignored': True}),
                headers=[('Content-Type', 'application/json')],
                status=200,
            )

        try:
            self._store_webhook_payload(payload)
        except Exception:
            _logger.exception("Failed to store incoming WhatsApp webhook payload (event=%s)", event_name)
            return request.make_response(
                json.dumps({'ok': False, 'error': 'internal_error'}),
                headers=[('Content-Type', 'application/json')],
                status=500,
            )

        return request.make_response(
            json.dumps({'ok': True}),
            headers=[('Content-Type', 'application/json')],
            status=200,
        )
