# -*- coding: utf-8 -*-
import logging

import requests

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WhatsappAccountConnectWizard(models.TransientModel):
    """Popup dialog that shows the Evolution API (Baileys) pairing QR code,
    exactly like WhatsApp Web's "Scan to log in" screen. This is a
    *different* thing from wa.qr.code (the click-to-chat QR the customer
    scans to open a wa.me chat): this one is scanned by the BUSINESS phone
    to link the Evolution instance itself.

    Evolution API exposes this on GET /instance/connect/{instance}, which
    returns {"pairingCode": "...", "code": "...", "base64": "data:image/png;
    base64,...", "count": N}. There's no websocket push into Odoo, so the
    dialog is refreshed/checked manually with the two buttons below.
    """
    _name = 'whatsapp.account.connect.wizard'
    _description = 'Link WhatsApp Device'

    account_id = fields.Many2one('whatsapp.account', required=True, readonly=True)
    qr_image = fields.Binary(string="QR Code", readonly=True)
    pairing_code = fields.Char(string="Pairing Code", readonly=True)
    connection_state = fields.Selection([
        ('connecting', 'Waiting for scan'),
        ('open', 'Connected'),
        ('close', 'Not connected'),
    ], default='connecting', readonly=True)

    # ------------------------------------------------------------------
    # QR fetching
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        account_id = self.env.context.get('default_account_id')
        if account_id:
            account = self.env['whatsapp.account'].browse(account_id)
            vals.update(self._fetch_qr(account))
        return vals

    def _fetch_qr(self, account):
        """Calls GET /instance/connect/{instance} and returns write-ready
        values for qr_image / pairing_code / connection_state."""
        if not account.server_url or not account.instance_name or not account.api_key:
            raise ValidationError(_(
                "Please fill in the Server URL, Instance Name and API Key first."))
        url = account._evo_instance_url("instance/connect")
        response = requests.get(url, headers=account._evo_headers(), timeout=15)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if account.developer_mode:
            _logger.info("Evolution API instance/connect response: %s", data)
        if response.status_code != 200:
            raise ValidationError(_("Could not get QR Code: %s") % (data.get('message') or response.text))

        # Some Evolution versions nest the payload under "qrcode", others
        # return it flat - support both.
        qrcode_data = data.get('qrcode') or data
        base64_img = qrcode_data.get('base64')
        pairing_code = qrcode_data.get('pairingCode')
        qr_binary = False
        if base64_img:
            # Field comes as a full data URI ("data:image/png;base64,...."),
            # but the Binary widget only wants the base64 payload itself.
            qr_binary = base64_img.split(',', 1)[1] if ',' in base64_img else base64_img

        # The connection state can show up in different shapes depending on
        # the Evolution version/endpoint: {"status": "..."} on some, nested
        # under {"instance": {"state": "..."}} on others (this is the same
        # shape /instance/connectionState uses).
        state = (
            data.get('status')
            or (data.get('instance') or {}).get('state')
            or qrcode_data.get('status')
            or 'connecting'
        )
        if state not in ('connecting', 'open', 'close'):
            state = 'connecting'

        if not qr_binary and state != 'open':
            raise ValidationError(_(
                "The instance did not return a QR code (status: %s). It may already be "
                "connected, or the Evolution server is still starting the session - try "
                "Refresh in a few seconds."
            ) % state)

        return {
            'account_id': account.id,
            'qr_image': qr_binary,
            'pairing_code': pairing_code,
            'connection_state': state,
        }

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_refresh(self):
        self.ensure_one()
        self.write(self._fetch_qr(self.account_id))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'whatsapp.account.connect.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_check_connection(self):
        """Polls /instance/connectionState - since there's no push channel,
        the user clicks this after scanning to confirm the link succeeded."""
        self.ensure_one()
        account = self.account_id
        url = account._evo_instance_url("instance/connectionState")
        response = requests.get(url, headers=account._evo_headers(), timeout=15)
        try:
            data = response.json()
        except ValueError:
            data = {}
        state = (data.get('instance') or {}).get('state') or data.get('state') or 'close'

        if state == 'open':
            self.connection_state = 'open'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Connected"),
                    'message': _("WhatsApp is now linked to instance '%s'.") % account.instance_name,
                    'type': 'success',
                    'sticky': False,
                    # Closes the QR popup, then opens the Discuss app so the
                    # user lands straight on the WhatsApp-Web-style
                    # conversations screen instead of just seeing a toast.
                    'next': {
                        'type': 'ir.actions.client',
                        'tag': 'mail.action_discuss',
                    },
                }
            }

        self.connection_state = 'close' if state == 'close' else 'connecting'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Not connected yet"),
                'message': _("Still waiting for the QR code to be scanned (state: %s). "
                             "If the QR looks stale, click Refresh QR.") % state,
                'type': 'warning',
                'sticky': False,
            }
        }