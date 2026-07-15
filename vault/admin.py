from django.contrib import admin
from .models import UserProfile,PaymentCard,AuditEvent,SecurityAlert
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display=('user','role','active')
@admin.register(PaymentCard)
class PaymentCardAdmin(admin.ModelAdmin):
    list_display=('client_name','brand','last4','active','created_by','created_at')
    readonly_fields=('encrypted_pan','encrypted_expiry','last4','created_by')
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False
@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display=('created_at','user','action','card','outside_office_hours')
    readonly_fields=[f.name for f in AuditEvent._meta.fields]
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False
@admin.register(SecurityAlert)
class SecurityAlertAdmin(admin.ModelAdmin):
    list_display=('created_at','event','status')
