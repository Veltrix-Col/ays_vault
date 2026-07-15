from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django import forms

from .models import AuditEvent, SecurityAlert, UserProfile
from .security import audit


class SecurityAlertAdminForm(forms.ModelForm):
    class Meta:
        model = SecurityAlert
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") in {"JUSTIFIED", "CLOSED"} and not (cleaned.get("review_note") or "").strip():
            raise forms.ValidationError("Debe registrar un comentario para justificar o cerrar una alerta.")
        return cleaned


admin.site.site_header = "A&S Vault — Administración"
admin.site.site_title = "A&S Vault"
admin.site.index_title = "Administración restringida"


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class VaultUserAdmin(UserAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        safe_fields = [name for name in form.changed_data if name not in {"password"}]
        audit(request, "UPDATE", reason="Cambio administrativo de usuario", metadata={"target_user_id": obj.pk, "changed_fields": safe_fields})

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "active", "mfa_enabled")
    list_filter = ("role", "active", "mfa_enabled")
    readonly_fields = ("mfa_enabled", "password_changed_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit(request, "UPDATE", reason="Cambio administrativo de perfil", metadata={"profile_user_id": obj.user_id, "changed_fields": list(form.changed_data)})

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("sequence", "created_at", "user", "action", "card", "result", "risk_level")
    readonly_fields = [field.name for field in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SecurityAlert)
class SecurityAlertAdmin(admin.ModelAdmin):
    form = SecurityAlertAdminForm
    list_display = ("created_at", "event", "status")
    readonly_fields = ("event", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit(request, "UPDATE", reason="Cambio de estado de alerta", metadata={"alert_id": obj.pk, "status": obj.status})
