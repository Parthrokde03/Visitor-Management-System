/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListRenderer } from "@web/views/list/list_renderer";
import { VisitorDashboard } from '@visitor_management/views/visitor_dashboard';

export class VisitorDashboardRenderer extends ListRenderer {}

VisitorDashboardRenderer.template = 'visitor_management.VisitorListView';
VisitorDashboardRenderer.components = Object.assign({}, ListRenderer.components, { VisitorDashboard });

export const VisitorDashboardListView = {
    ...listView,
    Renderer: VisitorDashboardRenderer,
};

registry.category("views").add("visitor_dashboard_list", VisitorDashboardListView);
