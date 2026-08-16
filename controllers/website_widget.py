# -*- coding: utf-8 -*-
import base64
import json
import logging

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WhatsappWidgetController(http.Controller):
    """
    ويدجت الواتساب المدمج في الموقع (falcon_website): شات حي بالكامل جوه
    الموقع - الزائر يكتب اسمه ورقمه ورسالته، النظام يبعتها فعليًا كرسالة
    واتساب عن طريق Evolution API، والردود اللي بتوصل من فريقك (سواء من
    واتساب مباشرة أو من Discuss في أودوو) بتتعرض في نفس نافذة الشات عن
    طريق polling على conversation_token (مش رقم التليفون مباشرة، عشان
    محدش يقدر يشوف محادثة غيره لو خمّن رقم).
    """

    # Widget accepts images, audio recordings and documents (PDF, docx...).
    # Kept generous but bounded - the actual per-file size is also limited
    # by Evolution API / WhatsApp itself for real phone-number conversations.
    _MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB

    def _get_widget_upload(self):
        """Returns the uploaded werkzeug FileStorage from the widget's file
        input (image, voice recording or document), or False if none was
        sent / it exceeds the size limit."""
        upload = request.httprequest.files.get('file')
        if not upload or not upload.filename:
            return False
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > self._MAX_UPLOAD_SIZE:
            raise ValidationError(_("File is too large (max %s MB)") % (self._MAX_UPLOAD_SIZE // (1024 * 1024)))
        return upload

    @http.route('/api/v1/whatsapp/widget/start', type='http', auth='public',
                methods=['POST'], csrf=False, cors="*")
    def widget_start(self, **kwargs):
        name = (kwargs.get('name') or '').strip()
        phone = (kwargs.get('phone') or '').strip()
        message = (kwargs.get('message') or '').strip()
        try:
            upload = self._get_widget_upload()
        except ValidationError as e:
            return self._json_response({'ok': False, 'error': str(e)}, status=400)
        if not message and not upload:
            return self._json_response({'ok': False, 'error': 'message or file is required'}, status=400)
        try:
            # Always open the conversation with a text message first (even a
            # placeholder) so a conversation_token/lead exists, then attach
            # the file to it the same way a reply would.
            result = request.env['wa.message'].sudo().start_website_conversation(
                name, phone, message or _("[Attachment]"))
            if upload:
                media_result = request.env['wa.message'].sudo().reply_to_conversation_with_media(
                    result['conversation_token'], '', upload)
                result['last_message_id'] = media_result['last_message_id']
        except ValidationError as e:
            return self._json_response({'ok': False, 'error': str(e)}, status=400)
        except Exception:
            _logger.exception("Failed to start website WhatsApp conversation")
            return self._json_response({'ok': False, 'error': 'internal_error'}, status=500)
        result['ok'] = True
        return self._json_response(result)

    @http.route('/api/v1/whatsapp/widget/reply', type='http', auth='public',
                methods=['POST'], csrf=False, cors="*")
    def widget_reply(self, **kwargs):
        token = (kwargs.get('token') or '').strip()
        message = (kwargs.get('message') or '').strip()
        try:
            upload = self._get_widget_upload()
        except ValidationError as e:
            return self._json_response({'ok': False, 'error': str(e)}, status=400)
        if not token or (not message and not upload):
            return self._json_response({'ok': False, 'error': 'token and message or file are required'}, status=400)
        try:
            if upload:
                result = request.env['wa.message'].sudo().reply_to_conversation_with_media(
                    token, message, upload)
            else:
                result = request.env['wa.message'].sudo().reply_to_conversation(token, message)
        except ValidationError as e:
            return self._json_response({'ok': False, 'error': str(e)}, status=400)
        except Exception:
            _logger.exception("Failed to send website WhatsApp reply")
            return self._json_response({'ok': False, 'error': 'internal_error'}, status=500)
        result['ok'] = True
        return self._json_response(result)

    @http.route('/api/v1/whatsapp/widget/messages', type='http', auth='public',
                methods=['GET'], csrf=False, cors="*")
    def widget_messages(self, token=None, after=0, **kwargs):
        if not token:
            return self._json_response({'ok': False, 'error': 'token is required'}, status=400)
        try:
            after_id = int(after) if after else 0
        except (TypeError, ValueError):
            after_id = 0
        messages = request.env['wa.message'].sudo().get_conversation(token, after_id)
        return self._json_response({'ok': True, 'messages': messages})

    @http.route('/api/v1/whatsapp/widget/media/<int:message_id>', type='http', auth='public',
                methods=['GET'], csrf=False, cors="*")
    def widget_media(self, message_id, token=None, **kwargs):
        """Serves an image/video/document attached to a wa.message so the
        website widget can render it. Not /web/image on purpose - that
        enforces normal ir.rule/ACL access anonymous visitors don't have.
        Protected instead by requiring the same conversation_token the
        widget already polls with, so a visitor can only fetch media that
        belongs to their own conversation."""
        wa = request.env['wa.message'].sudo().browse(message_id)
        if not wa.exists() or not token or not wa.conversation_token or wa.conversation_token != token or not wa.media_data:
            _logger.info(
                "WhatsApp widget media 404: id=%s exists=%s token_given=%s token_match=%s has_media=%s",
                message_id, wa.exists(), bool(token),
                (wa.exists() and wa.conversation_token == token), bool(wa.exists() and wa.media_data))
            return request.not_found()
        try:
            data = base64.b64decode(wa.media_data)
        except Exception:
            return request.not_found()
        headers = [
            ('Content-Type', wa.media_mimetype or 'application/octet-stream'),
            ('Content-Disposition', 'inline; filename="%s"' % (wa.media_filename or 'file')),
            ('Content-Length', len(data)),
        ]
        return request.make_response(data, headers=headers)

    @staticmethod
    def _json_response(data, status=200):
        body = json.dumps(data)
        headers = [('Content-Type', 'application/json')]
        return request.make_response(body, headers=headers, status=status)