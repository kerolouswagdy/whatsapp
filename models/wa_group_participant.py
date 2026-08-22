# -*- coding: utf-8 -*-
from odoo import fields, models


class WaGroupParticipant(models.Model):
    """كاش خفيف لاسم وصورة كل عضو ظهر في جروب واتساب - عشان شاشة الشات
    الجديدة تقدر تعرض اسم المرسل الحقيقي (مش رقمه) وصورته فوق كل رسالة
    جروب، زي واتساب بالظبط، من غير ما نلوث جهات اتصال res.partner/crm.lead
    بأعضاء جروبات ممكن ميكونوش عملاء أصلاً.

    مفتاح البحث هو رقم التليفون (بعد normalize_phone) + الشركة، عشان لو
    نفس الشخص ظاهر في أكتر من جروب يتكاش مرة واحدة بس."""
    _name = 'wa.group.participant'
    _description = 'WhatsApp Group Participant Cache'
    _rec_name = 'display_name'

    phone_number = fields.Char(required=True, index=True)
    display_name = fields.Char()
    avatar_url = fields.Char(
        help="رابط صورة البروفايل بتاعت الشخص ده على واتساب (من "
             "chat/fetchProfilePictureUrl على Evolution API) - بيتخزن اللينك "
             "بس، مش بايتات الصورة، عشان يتحمّل مباشرة في الـ <img>.")
    avatar_fetched_date = fields.Datetime(
        help="آخر مرة اتجابت فيها الصورة من Evolution - بنعيد الجلب كل "
             "كام يوم بدل كل رسالة، توفيرًا لعدد الـ API calls.")
    company_id = fields.Many2one('res.company')

    _sql_constraints = [
        ('phone_company_unique', 'unique(phone_number, company_id)',
         'This participant is already cached for this company.'),
    ]
