# -*- coding: utf-8 -*-
import base64
import io
import logging
from urllib.parse import quote

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

try:
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None
    _logger.warning(
        "Python package 'qrcode' is not installed. WhatsApp QR Code generation "
        "will not work until you run: pip install qrcode[pil]"
    )


class WaQrCode(models.Model):
    """WhatsApp 'Click to Chat' QR codes.

    NOTE: Evolution API (Baileys) has no server-side equivalent of Meta's
    Graph API message_qrdls endpoint - there is no remote 'QR code' object
    to create/refresh/delete on a server. Instead, this now builds a
    standard wa.me deep link (https://wa.me/<phone>?text=<message>) locally
    using the account's phone_number, and renders the QR image for that
    link locally with the 'qrcode' python package. No external API call
    is made, so there is nothing to keep in sync with a remote service.
    """
    _name = "wa.qr.code"
    _description = "WhatsApp QR Code"
    _order = "create_date desc"

    name = fields.Char(
        required=True,
        help="Internal label to recognize this code, e.g. the campaign, "
             "flyer or location it's used for.")
    account_id = fields.Many2one('whatsapp.account', string="Account", required=True, ondelete='cascade')
    prefilled_message = fields.Text(
        required=True,
        help="Message automatically filled in when someone scans the code and opens WhatsApp.")
    deep_link_url = fields.Char(string="wa.me Link", readonly=True, copy=False)
    qr_image = fields.Binary(string="QR Code", readonly=True, copy=False, attachment=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('error', 'Error'),
    ], default='draft', copy=False)
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Local generation - no remote calls, nothing to keep in sync
    # ------------------------------------------------------------------
    def _build_deep_link(self):
        self.ensure_one()
        account = self.account_id
        phone = (account.phone_number or '').replace(' ', '').replace('+', '').replace('-', '')
        if not phone:
            raise ValidationError(_(
                "Please fill in the Phone Number on the WhatsApp Account first."))
        text = quote(self.prefilled_message or '')
        return "https://wa.me/%s?text=%s" % (phone, text)

    def _render_qr_image(self, url):
        if qrcode is None:
            raise ValidationError(_(
                "The 'qrcode' python package is not installed on the server. "
                "Ask your administrator to run: pip install qrcode[pil]"))
        img = qrcode.make(url)
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        return base64.b64encode(buffer.getvalue())

    def action_generate(self):
        """Builds the wa.me deep link and renders its QR image locally."""
        for qr in self:
            try:
                url = qr._build_deep_link()
                image = qr._render_qr_image(url)
            except ValidationError:
                qr.state = 'error'
                raise
            qr.write({
                'deep_link_url': url,
                'qr_image': image,
                'state': 'generated',
            })
        return True

    def action_refresh(self):
        """Re-builds the deep link/image, e.g. after editing prefilled_message
        or the account's phone number."""
        return self.action_generate()

    def unlink(self):
        # Nothing remote to clean up anymore - purely local records.
        return super().unlink()