from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django import forms

from .models import AccessException, AlertTransition, AuditEvent, AuditVerificationRun, Holiday, NotificationRecipient, NotificationRecord, PolicyConfiguration, PolicyEvaluationRun, SecurityAlert, UserProfile
from .security import audit
from .identity import has_recent_reauth
from django_otp.plugins.otp_totp.models import TOTPDevice


class SecurityAlertAdminForm(forms.ModelForm):
    class Meta:
        model = SecurityAlert
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") in {"JUSTIFIED", "CLOSED"} and not (cleaned.get("review_note") or "").strip():
            raise forms.ValidationError("Debe registrar un comentario para justificar o cerrar una alerta.")
        return cleaned


admin.site.site_header = "CardManager — Administración"
admin.site.site_title = "CardManager"
admin.site.index_title = "Administración restringida"

if admin.site.is_registered(TOTPDevice):
    admin.site.unregister(TOTPDevice)


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class VaultUserAdmin(UserAdmin):
    def user_change_password(self, request, id, form_url=""):
        raise PermissionDenied("Use el flujo seguro de cambio de contraseña de CardManager.")

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and has_recent_reauth(request, "identity_admin")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and has_recent_reauth(request, "identity_admin")
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        safe_fields = [name for name in form.changed_data if name not in {"password"}]
        audit(request, "UPDATE", reason="Cambio administrativo de usuario", metadata={"target_user_id": obj.pk, "changed_fields": safe_fields})
        if "is_active" in safe_fields and not obj.is_active:
            from .identity import invalidate_authorizations, revoke_session
            invalidate_authorizations(obj)
            for record in obj.vault_sessions.filter(status="ACTIVE"):
                revoke_session(record, actor=request.user, reason="Usuario desactivado administrativamente", request=request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "active", "mfa_enabled")
    list_filter = ("role", "active", "mfa_enabled")
    readonly_fields = ("mfa_enabled", "mfa_status", "mfa_failed_attempts", "mfa_changed_at", "password_changed_at")

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and has_recent_reauth(request, "identity_admin")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit(request, "UPDATE", reason="Cambio administrativo de perfil", metadata={"profile_user_id": obj.user_id, "changed_fields": list(form.changed_data)})
        if set(form.changed_data) & {"role", "active"}:
            from .identity import invalidate_authorizations, revoke_session
            invalidate_authorizations(obj.user)
            for record in obj.user.vault_sessions.filter(status="ACTIVE"):
                revoke_session(record, actor=request.user, reason="Cambio administrativo de rol o estado", request=request)

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

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit(request, "UPDATE", reason="Cambio de estado de alerta", metadata={"alert_id": obj.pk, "status": obj.status})


class ReadOnlyControlAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


admin.site.register(AccessException, ReadOnlyControlAdmin)
admin.site.register(AlertTransition, ReadOnlyControlAdmin)
admin.site.register(Holiday, ReadOnlyControlAdmin)
admin.site.register(NotificationRecord, ReadOnlyControlAdmin)
admin.site.register(PolicyEvaluationRun, ReadOnlyControlAdmin)
admin.site.register(AuditVerificationRun, ReadOnlyControlAdmin)
admin.site.register(PolicyConfiguration, ReadOnlyControlAdmin)
admin.site.register(NotificationRecipient, ReadOnlyControlAdmin)
