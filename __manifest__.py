# -*- coding: utf-8 -*-
{
    'name': "visitor_management",

    'summary': "Short (1 phrase/line) summary of the module's purpose",

    'description': """
Long description of module's purpose
    """,

    'author': "My Company",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base','mail','hr'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'security/visitor_security.xml',
        'data/sms.xml',
        'data/approved_mail_template.xml',
        'data/cancelled_mail_template.xml',
        'data/employee_mail_template.xml',
        'report/badge_report.xml',
        'views/visit_views.xml',
        'views/customfield_views.xml',
        'wizard/cancel_wizard_view.xml',
        'views/menu.xml',        
    ],
    
    'assets': {
    'web.assets_backend': [
        'visitor_management/static/src/views/visitor_dashboard.xml',
        'visitor_management/static/src/views/visitor_dashboard.js',
        'visitor_management/static/src/views/visitor_listview.xml',
        'visitor_management/static/src/views/visitor_listview.js',
    ],
},
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}

