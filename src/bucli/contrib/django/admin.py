from django.contrib import admin

from bucli.contrib.django.models import BillingAccount, ProcessedEvent


@admin.register(BillingAccount)
class BillingAccountAdmin(admin.ModelAdmin):
    list_display = ("account", "plan", "status", "current_period_end", "cancel_at_period_end", "livemode")
    list_filter = ("status", "plan", "livemode", "cancel_at_period_end")
    search_fields = ("bucli_customer_id", "bucli_subscription_id")
    readonly_fields = [field.name for field in BillingAccount._meta.fields if field.name != "account"]


@admin.register(ProcessedEvent)
class ProcessedEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "type", "outcome", "sequence", "livemode", "received_at")
    list_filter = ("type", "outcome", "livemode")
    search_fields = ("event_id",)
    readonly_fields = [field.name for field in ProcessedEvent._meta.fields]
