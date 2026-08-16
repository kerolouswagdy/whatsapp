# -*- coding: utf-8 -*-
import re

from odoo import fields, models, _, api
from odoo.exceptions import ValidationError

# Meta WhatsApp templates use {{1}}, {{2}}, ... as body placeholders.
PLACEHOLDER_RE = re.compile(r'\{\{\d+\}\}')


class WaMessageTemplate(models.Model):
    _name = "wa.message.template"
    _description = "WhatsApp Template"
    _order = "create_date desc"

    name = fields.Char(string="Name")
    account_id = fields.Many2one('whatsapp.account', string="Account", ondelete='cascade')
    content = fields.Text(string="Content")
    dialog_reference = fields.Char(
        string="Template Name",
        help="Technical name of the template exactly as approved in Meta Business Manager "
             "(WhatsApp Manager > Account Tools > Message Templates).")
    params_ids = fields.One2many('wa.message.template.params', 'template_id')
    button_ids = fields.One2many('wa.message.template.button', 'template_id', string="Buttons")
    model_id = fields.Many2one('ir.model', string="Applies to")
    model_name = fields.Char(related="model_id.name")
    lang_code = fields.Char(string="Language", default="en_US")
    category = fields.Selection([
        ('marketing', 'Marketing'),
        ('utility', 'Utility'),
        ('authentication', 'Authentication'),
    ], string="Category", default="marketing")
    phone_field_id = fields.Many2one(
        'ir.model.fields', string="Phone Field",
        domain="[('model_id', '=', model_id), '|', ('ttype', '=', 'char'), ('relation', '=', 'res.partner')]")
    user_ids = fields.Many2many(
        'res.users', string="Users",
        help="Leave empty to make this template accessible to all users.")
    header_type = fields.Selection([
        ('none', 'None'),
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('document', 'Document'),
    ], string="Header Type", default="none")
    footer_message = fields.Char(string="Footer Message")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string="Status", default="draft", tracking=True, copy=False)
    message_count = fields.Integer(compute="_compute_message_count")

    @api.depends()
    def _compute_message_count(self):
        for template in self:
            template.message_count = self.env['wa.message'].search_count(
                [('wa_message_template_id', '=', template.id)])

    def get_params_values(self, res_id=False):
        res = []
        rec = self.env[self.model_id.model].browse(res_id)
        if rec:
            for param in self.params_ids:
                if param.type == 'custom_text':
                    res.append(param.custom_text)
                else:
                    txt = False
                    if param.field_id.ttype == 'char':
                        txt = rec[param.field_id.name]
                    elif param.field_id.ttype == 'many2one':
                        txt = rec[param.field_id.name].display_name
                    else:
                        raise ValidationError(_("Field Type Error. Reach Admin"))
                    if not txt:
                        txt = param.not_found_content
                    res.append(txt)
        return res

    @api.constrains('content', 'params_ids')
    def check_len_inputs(self):
        for template in self:
            content = template.content or ''
            placeholder_count = len(PLACEHOLDER_RE.findall(content))
            if len(template.params_ids) != placeholder_count:
                raise ValidationError(_(
                    "Number of parameters (%(params)s) does not match the number of placeholders (%(placeholders)s)"
                ) % {'params': len(template.params_ids), 'placeholders': placeholder_count})

    def get_sending_txt(self, params):
        content = self.content or ''
        placeholder_count = len(PLACEHOLDER_RE.findall(content))
        if len(params) != placeholder_count:
            raise ValidationError(_(
                "Number of parameters (%(params)s) does not match the number of placeholders (%(placeholders)s)"
            ) % {'params': len(params), 'placeholders': placeholder_count})
        for index, param in enumerate(params, start=1):
            content = content.replace('{{%d}}' % index, param or '')
        return content

    # ------------------------------------------------------------------
    # Buttons / actions
    # ------------------------------------------------------------------
    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Messages'),
            'res_model': 'wa.message',
            'view_mode': 'list,form',
            'domain': [('wa_message_template_id', '=', self.id)],
        }

    def action_submit_for_approval(self):
        self.write({'state': 'pending'})

    def action_set_approved(self):
        self.write({'state': 'approved'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    def action_preview(self):
        self.ensure_one()
        sample_params = [
            param.custom_text or param.not_found_content or _("Sample %s") % (index + 1)
            for index, param in enumerate(self.params_ids)
        ]
        try:
            text = self.get_sending_txt(sample_params) if sample_params else (self.content or '')
        except ValidationError:
            text = self.content or ''
        if self.footer_message:
            text += '\n\n' + self.footer_message
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Preview"),
                'message': text or _("This template has no content yet."),
                'sticky': True,
            }
        }


class WaMessageTemplateParams(models.Model):
    _name = "wa.message.template.params"
    _description = "WhatsApp Template Variable"
    _order = "sequence"

    sequence = fields.Integer()
    model_id = fields.Many2one(related="template_id.model_id")
    model_name = fields.Char(related="model_id.name")
    template_id = fields.Many2one('wa.message.template')
    type = fields.Selection(selection=[('custom_text', 'Custom text'), ('model_field', 'Model Field')])
    field_id = fields.Many2one('ir.model.fields')
    custom_text = fields.Text()
    not_found_content = fields.Char()


class WaMessageTemplateButton(models.Model):
    _name = "wa.message.template.button"
    _description = "WhatsApp Template Button"
    _order = "sequence"

    sequence = fields.Integer()
    template_id = fields.Many2one('wa.message.template', required=True, ondelete='cascade')
    button_type = fields.Selection([
        ('quick_reply', 'Quick Reply'),
        ('url', 'Visit Website'),
        ('phone_number', 'Call Phone Number'),
    ], string="Type", default="quick_reply", required=True)
    text = fields.Char(string="Button Text", required=True)
    value = fields.Char(string="URL / Phone Number")