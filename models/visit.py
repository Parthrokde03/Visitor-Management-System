# -*- coding: utf-8 -*-

import uuid
from lxml import etree
from odoo import models, fields, api,_
from odoo.exceptions import UserError
from odoo.exceptions import ValidationError
import re
import base64
import logging
import requests
from odoo.addons.visitor_management.controllers.api import SMSUtils

_logger = logging.getLogger(__name__)


class VisitInformation(models.Model):
    _name = 'visit.information'
    _inherit = ['mail.thread','mail.activity.mixin']
    _description = 'Visit Information'
    _rec_name = 'name'

    name = fields.Char(string="Name")
    check_in = fields.Datetime(string="Check-in",readonly=True)
    company = fields.Char(string="Company")
    company_id = fields.Many2one('res.company',string="Employee Company",default=lambda self: self.env.company,   required=True, readonly=True,index=True)
    employee = fields.Many2one('hr.employee',string='Employee',default=lambda self: self._default_employee())
    check_out = fields.Datetime(string="Check-out",readonly=True)
    status = fields.Selection([("pending","Pending"),("approved","Approved"),("cancelled","Cancelled")],default="pending",tracking=True)
    email = fields.Char(string="Email")
    phone = fields.Char(string="Phone", required=True, size=10)
    purpose = fields.Text(string="Purpose of Visit")
    cancellation_reason = fields.Text(string="Cancellation Reason")
    otp_code = fields.Char(string="OTP", store=True)
    otp_attempts = fields.Integer(string="OTP Attempts", default=0)
    last_otp_time = fields.Datetime(string="Last OTP Sent")
    location_id = fields.Many2one("company.location",string="Location",domain="[('company_id', '=', company_id)]")
    attachment_id = fields.Many2one("ir.attachment")
    visiting_date = fields.Datetime(string="Date")
    qr_token = fields.Char("QR Token", default=lambda self: str(uuid.uuid4()), readonly=True)
    instructions = fields.Text(string="Instruction")
    visit_type = fields.Selection([
    ("pre", "Pre-Registered"),
    ("walkin", "Walk-In")
    ], default="pre", string="Visitor Type")
    nda_answer = fields.Image(string="Signature",max_width=1024,max_height=768,verify_resolution=True)
    photo_answer = fields.Image(string="Photo",max_width=1024,max_height=768,verify_resolution=True)
    notebook_id = fields.One2many(
        'visitor.notebook.entry', 'visitor_id', string="Notebook Entries",ondelete='cascade'
    )
    
    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        _logger.info("fields_view_get called for visit.information")
        result = super().fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
 
        if view_type == 'form':
            doc = etree.XML(result['arch'])
 
            # Find your target <group> in the inherited view
            group = doc.xpath("//group[@name='custom_fields']")
            if group:
                group = group[0]
                existing_fields = {node.get("name") for node in group.xpath(".//field")}
 
                # Get all fields of this model
                all_fields = self._fields.keys()
 
                # Filter out technical ones
                auto_fields = [f for f in all_fields if f not in existing_fields and not f.startswith("_")]
 
                # Dynamically add missing fields
                for field in auto_fields:
                    # Only add custom fields or new fields you want
                    if self._fields[field].manual:  # `manual=True` means created via UI
                        new_field = etree.Element("field", name=field)
                        group.append(new_field)
 
                result['arch'] = etree.tostring(doc, encoding='unicode')
 
        return result

    

    @api.model_create_multi
    def create(self, vals_list):
        records = super(VisitInformation, self).create(vals_list)
        for rec in records:
            if rec.visit_type == 'walkin':
                rec._ask_additional_questions()
        return records

    
    def _ask_additional_questions(self):
        if self.location_id:
            for question in self.location_id.additional_question_ids:
                self.env['visitor.notebook.entry'].create({
                    'visitor_id': self.id,
                    'question_id': question.id,
                    'answer_selection': None,
                })


    
    @api.depends("employee")
    def _compute_company_id(self):
        for rec in self:
            rec.company_id = rec.employee.company_id if rec.employee else False
            
    def get_dynamic_fields(self):
        """Return company-configured fields"""
        company = self.env.company
        configs = company.visitor_field_ids.filtered(lambda c: c.enabled)
        return configs.mapped("field_name")
    
    @api.model
    def _default_employee(self):
        """Return the current user’s employee record if it exists."""
        user = self.env.user
        employee = self.env['hr.employee'].search([('user_id', '=', user.id)], limit=1)
        return employee.id if employee else False
        
    @api.constrains('phone')
    def _check_phone(self):
        for rec in self:
            if rec.phone and not re.fullmatch(r'\d{10}', rec.phone):
                raise ValidationError("Phone number must be exactly 10 digits.")

    @api.constrains('email')
    def _check_email(self):
        for rec in self:
            if rec.email and not re.fullmatch(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', rec.email):
                raise ValidationError("Please enter a valid email address.")
            
    def action_approved(self):
        for rec in self:
            # Always mark approved
            rec.sudo().write({'status': 'approved'})

            # Walk-in visitor
            if rec.visit_type == 'walkin':
                if not rec.check_in:
                    rec.sudo().write({'check_in': fields.Datetime.now()})
                    _logger.info("Auto check-in for walk-in visitor %s (id=%s)", rec.name, rec.id)

                # notify employee in Odoo dashboard
                if rec.employee and rec.employee.user_id:
                    message = {
                        'title': "Visitor Auto Check-in",
                        'message': f"{rec.name} has been auto checked-in at {rec.check_in.strftime('%H:%M')}.",
                        'sticky': True,
                        'type': 'info'
                    }
                    self.env['bus.bus']._sendone(
                        rec.employee.user_id.partner_id,
                        'simple_notification',
                        message
                    )
                # continue

            template = self.env.ref("visitor_management.email_visit_approved", False)
            report = self.env.ref("visitor_management.action_visit_report", False)

            if not (template and report):
                _logger.warning("Missing template or report for pre-registered visitor %s", rec.id)
                continue

            # 1. Generate PDF
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'visitor_management.action_visit_report', rec.id
            )
            pdf_base64 = base64.b64encode(pdf_content)

            # 2. Create attachment
            attachment = self.env['ir.attachment'].sudo().create({
                'name': f"Approved_Visit_{rec.name}.pdf",
                'type': 'binary',
                'datas': pdf_base64,
                'res_model': rec._name,
                'res_id': rec.id,
                'mimetype': 'application/pdf',
            })
            rec.sudo().write({'attachment_id': attachment.id})

            # 3. Attach to template
            template.attachment_ids = [(6, 0, [attachment.id])]

            # 4. Send SMS with download link
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            download_link = f"{base_url}/web/content/{attachment.id}?download=True"
            try:
                sms_text = f"Hi {rec.name}, your visit is approved. Download your pass here: {download_link}"
                _logger.info("Sending SMS to %s: %s", rec.phone, sms_text)
                SMSUtils.send_sms_route_mobile(self.env, rec.phone, "91", sms_text)
            except Exception:
                _logger.exception("Failed to send SMS for visitor %s", rec.id)

            # 5. Send email
            try:
                email_values = {'email_from': self.env.user.email}
                template.sudo().send_mail(rec.id, force_send=True, email_values=email_values)
            except Exception:
                _logger.exception("Failed to send approval email for visitor %s", rec.id)

        return True

    
    # Cancelled Method        
    def action_cancelled(self):
        return {
            "name": _("Cancellation Reason"),
            "type": "ir.actions.act_window",
            "res_model": "visit.cancel.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
            "default_cancel": self.cancellation_reason,
            "active_model": self._name,
            "active_id": self.id,
            },
        }   
        
    # Odoo Dashboard Method   
    @api.model
    def get_dashboard_data(self):
        """Return counts of visits by status for today"""
        today = fields.Date.today()
        start_of_day = fields.Datetime.to_datetime(f"{today} 00:00:00")
        end_of_day = fields.Datetime.to_datetime(f"{today} 23:59:59")

        counts = {}
        statuses = ["pending", "approved", "cancelled"]
        for status in statuses:
            counts[status] = self.search_count([
                ("status", "=", status),
                ("visiting_date", ">=", start_of_day),
                ("visiting_date", "<=", end_of_day),
            ])
        return counts
        
# Dynamic fields   
class CompanyField(models.Model):
    _name = "company.field"
    _description = "Visitor Field Configuration"

    location_id = fields.Many2one(
        "company.location", required=True, ondelete="cascade"
    )
    field_id = fields.Many2one(
        "ir.model.fields",
        domain="[('model_id.model', '=', 'visit.information'),('state','in',['manual','base'])]"
    )
    label = fields.Char("Label")
    enabled = fields.Boolean("Enabled", default=True)
    required = fields.Boolean("Required", default=False)
    field_type = fields.Selection([
        ("text","TEXT"),
        ("dropdown","Dropdown")
    ])


# Inherited seprately because many2many error
class CustomField(models.Model):
    _inherit = 'res.company'
    location_ids = fields.One2many(
        "company.location", "company_id", string="Locations"
    )      
    
class VisitorNotebookEntry(models.Model):
    _name = 'visitor.notebook.entry'
    _description = 'Visitor Notebook Entry'

    visitor_id = fields.Many2one('visit.information', string="Visitor", required=True, ondelete='cascade')
    question_id = fields.Many2one('company.location.question', string="Question", required=True)
    answer_selection = fields.Selection([
        ('yes', 'Yes'),
        ('no', 'No'),
    ], string="Answer Option")


class CompanyLocation(models.Model):
    _name = "company.location"
    _description = "Company Locations"

    company_id = fields.Many2one("res.company", required=True, ondelete="cascade")
    name = fields.Char("Location", required=True)

    # Main checkboxes
    nda = fields.Boolean("NDA")
    photo = fields.Boolean("Photo")
    question = fields.Boolean("Question")

    # Required checkboxes
    nda_required = fields.Boolean("Required")
    photo_required = fields.Boolean("Required")
    question_required = fields.Boolean("Required")

    nda_details = fields.Html()
    additional_question_ids = fields.One2many(
        "company.location.question", "location_id", string="Additional Questions"
    )
    visitor_field_ids = fields.One2many(
        "company.field", "location_id", string="Visitor Fields"
    )



class CompanyLocationQuestion(models.Model):
    _name = "company.location.question"
    _description = "Location Additional Questions"
    _rec_name = "question_text"


    location_id = fields.Many2one("company.location", required=True, ondelete="cascade")
    question_text = fields.Char("Question", required=True)
    question_type = fields.Selection([("checkbox", "Checkbox")], default="checkbox")
    required = fields.Boolean("Required", default=False)
