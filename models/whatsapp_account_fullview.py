# -*- coding: utf-8 -*-
import logging

import requests

from odoo import models, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WhatsappAccount(models.Model):
    """Adds the shared 'get me a QR / get me the connection state' helpers
    used by the full-page WhatsApp screen (see controllers/whatsapp_fullview
    _controller.py). Kept separate from wa_account_connect_wizard.py's own
    _fetch_qr so the wizard keeps working untouched; both call the same
    Evolution endpoints."""
    _inherit = "whatsapp.account"

    def fetch_connect_qr(self):
        """Same call as wa.account.connect.wizard._fetch_qr but returns a
        plain dict (no wizard record involved) - used to render the QR
        screen directly inside the full-page client action."""
        self.ensure_one()
        if not self.server_url or not self.instance_name or not self.api_key:
            raise ValidationError(_(
                "Please fill in the Server URL, Instance Name and API Key first."))
        url = self._evo_instance_url("instance/connect")
        response = requests.get(url, headers=self._evo_headers(), timeout=15)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code != 200:
            raise ValidationError(_("Could not get QR Code: %s") % (data.get('message') or response.text))

        qrcode_data = data.get('qrcode') or data
        base64_img = qrcode_data.get('base64')
        pairing_code = qrcode_data.get('pairingCode')
        qr_binary = False
        if base64_img:
            qr_binary = base64_img.split(',', 1)[1] if ',' in base64_img else base64_img

        state = (
            data.get('status')
            or (data.get('instance') or {}).get('state')
            or qrcode_data.get('status')
            or 'connecting'
        )
        if state not in ('connecting', 'open', 'close'):
            state = 'connecting'

        return {
            'qr_image': qr_binary,
            'pairing_code': pairing_code,
            'connection_state': state,
        }

    def get_connection_state(self):
        """GET /instance/connectionState/{instance} -> 'open' | 'close' | 'connecting'."""
        self.ensure_one()
        if not self.server_url or not self.instance_name or not self.api_key:
            return 'close'
        url = self._evo_instance_url("instance/connectionState")
        try:
            response = requests.get(url, headers=self._evo_headers(), timeout=15)
            data = response.json() if response.content else {}
        except (requests.RequestException, ValueError):
            return 'close'
        return (data.get('instance') or {}).get('state') or data.get('state') or 'close'
