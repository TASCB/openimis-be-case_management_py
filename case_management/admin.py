from django.contrib import admin

from .models import (
    FollowUpRemark, HouseholdDeactivation, MemberDeactivation,
    PaymentChangeAudit, PendingDataUpdate,
)

admin.site.register(PaymentChangeAudit)
admin.site.register(HouseholdDeactivation)
admin.site.register(MemberDeactivation)
admin.site.register(FollowUpRemark)
admin.site.register(PendingDataUpdate)
