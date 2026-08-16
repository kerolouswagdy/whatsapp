# -*- coding: utf-8 -*-
import base64
import json
import logging
import mimetypes

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WhatsAppFullViewController(http.Controller):
    """Endpoints بتخدم شاشة الشات الداخلية (static/src/js/whatsapp_full_view.js)
    فقط. auth='user' - شاشة داخلية لموظفين مسجلين دخول، الحساب بيتحدد من
    شركة اليوزر الحالي (wa.message.get_account())، مفيش account_id بييجي
    من الفرونت."""

    def _get_account(self):
        account = request.env['wa.message'].sudo().get_account()
        if not account:
            raise ValidationError("No WhatsApp Account configured for this company.")
        return account

    @http.route('/whatsapp/fullview/status', type='jsonrpc', auth='user')
    def fullview_status(self, **kwargs):
        account = self._get_account()
        state = account.get_connection_state()
        result = {'connection_state': state}
        if state != 'open':
            try:
                qr = account.fetch_connect_qr()
                result.update(qr)
            except ValidationError as e:
                result['error'] = str(e)
        return result

    @http.route('/whatsapp/fullview/conversations', type='jsonrpc', auth='user')
    def fullview_conversations(self, **kwargs):
        return request.env['wa.message'].sudo().get_conversations()

    @http.route('/whatsapp/fullview/messages', type='jsonrpc', auth='user')
    def fullview_messages(self, phone_number, after_id=0, before_id=0, **kwargs):
        return request.env['wa.message'].sudo().get_conversation_messages(
            phone_number, int(after_id or 0), int(before_id or 0))

    @http.route('/whatsapp/fullview/send', type='jsonrpc', auth='user')
    def fullview_send(self, phone_number, body, **kwargs):
        return request.env['wa.message'].sudo().send_from_fullview(phone_number, body)

    @http.route('/whatsapp/fullview/mark_read', type='jsonrpc', auth='user')
    def fullview_mark_read(self, phone_number, **kwargs):
        return request.env['wa.message'].sudo().mark_conversation_read(phone_number)

    @http.route('/whatsapp/fullview/toggle_favorite', type='jsonrpc', auth='user')
    def fullview_toggle_favorite(self, phone_number, **kwargs):
        return request.env['wa.message'].sudo().toggle_favorite_conversation(phone_number)

    @http.route('/whatsapp/fullview/react', type='jsonrpc', auth='user')
    def fullview_react(self, message_id, emoji='', **kwargs):
        return request.env['wa.message'].sudo().react_from_fullview(int(message_id), emoji or '')

    @http.route('/whatsapp/fullview/forward', type='jsonrpc', auth='user')
    def fullview_forward(self, message_id, to_phone_number, **kwargs):
        return request.env['wa.message'].sudo().forward_from_fullview(int(message_id), to_phone_number)

    @http.route('/whatsapp/fullview/delete_local', type='jsonrpc', auth='user')
    def fullview_delete_local(self, message_id, **kwargs):
        return request.env['wa.message'].sudo().delete_locally_from_fullview(int(message_id))

    @http.route('/whatsapp/fullview/sync_history', type='jsonrpc', auth='user')
    def fullview_sync_history(self, phone_number, **kwargs):
        return request.env['wa.message'].sudo().sync_history_from_fullview(phone_number)

    @http.route('/whatsapp/fullview/send_media', type='http', auth='user',
                methods=['POST'], csrf=False)
    def fullview_send_media(self, phone_number, caption='', **kwargs):
        """multipart/form-data (مش jsonrpc) عشان نقدر نبعت ملف حقيقي -
        الـ JS بيستخدم fetch() + FormData هنا بدل rpc()."""
        upload = request.httprequest.files.get('file')
        if not upload or not upload.filename:
            return self._json_response({'ok': False, 'error': 'file is required'}, status=400)
        try:
            msg = request.env['wa.message'].sudo().send_media_from_fullview(phone_number, upload, caption or '')
        except ValidationError as e:
            return self._json_response({'ok': False, 'error': str(e)}, status=400)
        except Exception:
            _logger.exception("Failed to send WhatsApp media from fullview")
            return self._json_response({'ok': False, 'error': 'internal_error'}, status=500)
        return self._json_response({'ok': True, 'message': msg})

    @staticmethod
    def _json_response(data, status=200):
        return request.make_response(
            json.dumps(data), headers=[('Content-Type', 'application/json')], status=status)

    @http.route('/whatsapp/fullview/media/<int:message_id>', type='http', auth='user')
    def fullview_media(self, message_id, **kwargs):
        wa = request.env['wa.message'].sudo().browse(message_id)
        if not wa.exists() or not wa.media_data:
            _logger.info("WhatsApp fullview media 404: id=%s exists=%s has_media=%s",
                         message_id, wa.exists(), bool(wa.exists() and wa.media_data))
            return request.not_found()
        try:
            data = base64.b64decode(wa.media_data)
        except Exception:
            _logger.exception("Failed to decode WhatsApp media for message %s", message_id)
            return request.not_found()
        mimetype = wa.media_mimetype
        if not mimetype:
            guessed, _enc = mimetypes.guess_type(wa.media_filename or '')
            mimetype = guessed or 'application/octet-stream'
        headers = [
            ('Content-Type', mimetype),
            ('Content-Disposition', 'inline; filename="%s"' % (wa.media_filename or 'file')),
            ('Content-Length', len(data)),
        ]
        return request.make_response(data, headers=headers)
