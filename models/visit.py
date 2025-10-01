# -*- coding: utf-8 -*-
import json
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
try:
    # available in modern Odoo versions
    from odoo.osv.orm import setup_modifiers
except Exception:
    setup_modifiers = None

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
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type != "form":
            return arch, view

        # arch may be string or lxml element (v18); keep/return same type
        is_element = isinstance(arch, etree._Element)
        doc = arch if is_element else etree.fromstring(arch)

        holder_nodes = doc.xpath("//group[@name='custom_fields']")
        if not holder_nodes:
            return (doc if is_element else etree.tostring(doc, encoding="unicode")), view
        holder = holder_nodes[0]

        # Company context (view is cached, so rely on default_company_id or current company)
        company_id = self.env.context.get('default_company_id') or self.env.company.id

        # Fetch *all* location-specific configs for this company
        cfgs = self.env['company.field'].sudo().search([
            ('enabled', '=', True),
            ('location_id.company_id', '=', company_id),
        ])

        # Avoid duplicate {field, location} injections if view already has some
        seen = {(n.get('name'), n.get('data-location')) for n in holder.xpath(".//field[@name]")}

        # Also track all fields already present in the *entire* form view
        existing_in_form = {n.get("name") for n in doc.xpath("//field[@name]")}

        # If 'view' is a dict (in some code paths), update field metadata so webclient can render
        fields_dict = view['fields'] if isinstance(view, dict) and 'fields' in view else None

        for cfg in cfgs:
            fname = cfg.field_id and cfg.field_id.name
            if not fname or fname not in self._fields:
                continue

            # Skip if this field already exists in form (avoid duplicates like "name")
            if fname in existing_in_form:
                _logger.info("Skipping field %s because it's already in the form", fname)
                continue

            loc_id = int(cfg.location_id.id)
            key = (fname, str(loc_id))
            if key in seen:
                continue

            # Ensure JS metadata (if accessible in this code path)
            if fields_dict is not None and fname not in fields_dict:
                fields_dict.update(self.fields_get([fname]))

            node = etree.Element("field", name=fname)
            node.set('data-location', str(loc_id))  # for debugging in DOM inspectors

            # Label override (per-location)
            if cfg.label:
                node.set('string', cfg.label)

            # Inline modifiers: visible only when this location is selected
            node.set('invisible', f"location_id != {loc_id}")

            # Only enforce required at view level if the model field itself isn’t required
            if cfg.required and not self._fields[fname].required:
                node.set('required', f"location_id == {loc_id}")

            # Compute modifiers so the web client honors attributes
            if setup_modifiers:
                setup_modifiers(node, self._fields[fname], context=self.env.context, in_tree_view=False)
            else:
                node.set('modifiers', json.dumps({}))  # safe fallback

            holder.append(node)
            seen.add(key)
            _logger.info("Injected dynamic field %s for location %s (required=%s)", fname, loc_id, cfg.required)
            print("&&&&&............>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>TESTING",fname)

        return (doc if is_element else etree.tostring(doc, encoding="unicode")), view

    
    # @api.model
    # def _get_view(self, view_id=None, view_type='form', **options):
    #     arch, view = super()._get_view(view_id, view_type, **options)

    #     if view_type != "form":
    #         return arch, view

    #     # Handle arch as string or element
    #     is_element = isinstance(arch, etree._Element)
    #     doc = arch if is_element else etree.fromstring(arch)

    #     # Find target group where to inject fields
    #     holder_nodes = doc.xpath("//group[@name='custom_fields']")
    #     if not holder_nodes:
    #         return (doc if is_element else etree.tostring(doc, encoding="unicode")), view
    #     holder = holder_nodes[0]

    #     # Fetch manual fields of this model
    #     manual_fields = self.env["ir.model.fields"].sudo().search([
    #         ("model", "=", self._name),
    #         ("state", "=", "manual"),
    #     ])

    #     _logger.info("Found manual fields for %s: %s", self._name, manual_fields.mapped("name"))

    #     # Track existing to avoid duplicates
    #     existing = {n.get("name") for n in holder.xpath(".//field[@name]")}

    #     # Inject manual fields into view
    #     for field in manual_fields:
    #         if field.name in existing:
    #             continue
    #         _logger.info("Injecting field: %s (%s)", field.name, field.field_description)

    #         # Ensure view dict knows about this field
    #         if isinstance(view, dict) and "fields" in view and field.name not in view["fields"]:
    #             view["fields"].update(self.fields_get([field.name]))

    #         node = etree.Element("field", name=field.name)
    #         node.set("string", field.field_description or field.name)
    #         node.set("invisible", "location_id == False")         
    #         node.set("modifiers", json.dumps({}))  # required in v16+

    #         holder.append(node)

    #     # Return same type as we got
    #     return (doc if is_element else etree.tostring(doc, encoding="unicode")), view


    

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
        return self.env['company.field'].sudo().search([
            ('enabled', '=', True),
            ('location_id.company_id', '=', self.env.company.id),
        ]).mapped(lambda c: c.field_id.name)

    
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

    
    location_id = fields.Many2one("company.location", required=True, ondelete="cascade")
    field_id = fields.Many2one(
        "ir.model.fields",
        domain="[('model_id.model', '=', 'visit.information'), ('state','in',['manual','base'])]"
    )
    company_id = fields.Many2one(
        'res.company',
        related='location_id.company_id',
        store=True,
        index=True,
        readonly=True,
    )
    label = fields.Char("Label")
    enabled = fields.Boolean("Enabled", default=True)
    required = fields.Boolean("Required", default=False)
    field_type = fields.Selection([
        ("text","TEXT"),
        ("dropdown","Dropdown")
    ])
    
    _sql_constraints = [
        ('uniq_location_field', 'unique(location_id, field_id)', 'This field is already configured for this location.')
    ]
    visitor_model_id = fields.Many2one(
        'ir.model',
        compute='_compute_visitor_model_id',
        store=True,
        readonly=True
    )

    @api.depends()
    def _compute_visitor_model_id(self):
        visit_model = self.env["ir.model"].sudo().search([("model", "=", "visit.information")], limit=1)
        for rec in self:
            rec.visitor_model_id = visit_model.id

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        visit_model = self.env["ir.model"].sudo().search([("model", "=", "visit.information")], limit=1)
        if visit_model:
            res["visitor_model_id"] = visit_model.id
        return res
        
        
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
