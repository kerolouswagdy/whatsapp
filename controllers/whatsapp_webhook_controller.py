# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)
_logger.info("odoo_whatsapp_api: whatsapp_webhook_controller module loaded "
             "(routes /api/v1/whatsapp/webhook and /api/v1/whatsapp/webhook/<event> are active)")


class WhatsAppWebhookController(http.Controller):
    """
    بيستقبل الـ POST requests اللي بتوصل من Evolution API (الـ Callback URL
    اللي اتسجل عن طريق action_subscribe_webhook أو اللي اتحط يدويًا في
    Evolution Manager > Events > Webhook).

    كل event (messages.upsert, messages.update, connection.update...) بيوصل
    كـ POST منفصل بالـ payload الخام، فبنخزنه زي ما هو في wa.webhook.messages
    وده بيشغّل message_process() تلقائيًا (compute field بيتفعّل وقت الـ create).

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
