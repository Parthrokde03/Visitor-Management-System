/** @odoo-module **/

import { useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart } from "@odoo/owl";

export class VisitorDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.state = useState({
            selectedStatus: null,
            statusCounts: { pending: 0, approved: 0, cancelled: 0 },
        });

        onWillStart(async () => {
            const counts = await this.orm.call("visit.information", "get_dashboard_data");
            this.state.statusCounts = counts;   // ✅ reactive assignment
        });
    }

    async setSearchContext(ev) {
        const status = ev.currentTarget.dataset.status;

        if (this.state.selectedStatus === status) {
            this.state.selectedStatus = null;
            this.env.searchModel.clearQuery();
            return;
        }

        this.state.selectedStatus = status;

        const today = new Date();
        const startOfDay = new Date(today.setHours(0, 0, 0, 0));
        const endOfDay = new Date(today.setHours(23, 59, 59, 999));

        const pad = (num) => String(num).padStart(2, '0');
        const formatOdooDateTime = (date) => {
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
        };

        const domain = [
            ['status', '=', status],
            ['visiting_date', '>=', formatOdooDateTime(startOfDay)],
            ['visiting_date', '<=', formatOdooDateTime(endOfDay)],
        ];

        this.env.searchModel.clearQuery();
        await this.env.searchModel.splitAndAddDomain(domain);
    }

    isActive(status) {
        return this.state.selectedStatus === status;
    }
}

VisitorDashboard.template = "visitor_management.VisitorDashboard";
