from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import ipaddress
import re

from .crypto import fingerprint
from .models import (
    AccessException,
    AuditEvent,
    Holiday,
    NotificationRecipient,
    PaymentCard,
    PolicyConfiguration,
    ReportExport,
)

REAUTHENTICATION_OPERATION_CHOICES = [
    ("REVEAL_PAN", "Revelar número de tarjeta"), ("REVEAL_EXPIRY", "Revelar vencimiento"),
    ("COPY_PAN", "Copiar número de tarjeta"), ("COPY_EXPIRY", "Copiar vencimiento"),
    ("CREATE_CARD", "Crear tarjeta"), ("EDIT_CARD", "Editar tarjeta"),
    ("DEACTIVATE_CARD", "Desactivar tarjeta"), ("CHANGE_PASSWORD", "Cambiar contraseña"),
    ("RESET_MFA", "Restablecer MFA"), ("CHANGE_POLICIES", "Cambiar políticas"),
    ("MANAGE_USERS", "Gestionar usuarios"), ("MANAGE_SESSIONS", "Gestionar sesiones"),
    ("MANAGE_DEVICES", "Gestionar dispositivos"), ("MANAGE_ALERTS", "Gestionar alertas"),
]
ACCESS_OPERATION_CHOICES = [
    ("LOGIN", "Iniciar sesión"), ("VIEW", "Consultar tarjetas"), ("REVEAL", "Revelar información"),
    ("COPY", "Copiar información"), ("CREATE", "Crear tarjeta"), ("EDIT", "Editar tarjeta"),
    ("DEACTIVATE", "Desactivar tarjeta"),
]
ALERT_TYPE_CHOICES = [
    ("OUTSIDE_HOURS", "Acceso fuera de horario"), ("NEW_DEVICE", "Dispositivo nuevo"),
    ("MFA_BLOCKED", "MFA bloqueado"), ("USER_BLOCKED", "Usuario bloqueado"),
    ("PASSWORD_CHANGE", "Cambio de contraseña"), ("MFA_RESET", "Reinicio de MFA"),
    ("POLICY_CHANGED", "Política modificada"), ("EXCEPTION_CREATED", "Excepción creada"),
    ("EXCEPTION_REVOKED", "Excepción revocada"), ("SYSTEM_INACTIVITY", "Sistema sin uso"),
    ("POSSIBLE_PARALLEL_TOOL_USE", "Posible uso paralelo de Excel"),
    ("AUDIT_INTEGRITY_REVIEW", "Fallo de integridad"), ("CRITICAL_ALERT", "Alerta crítica"),
    ("EMAIL_FAILURE", "Fallo de correo"),
]


def luhn_valid(number):
    total = 0
    parity = len(number) % 2
    for index, character in enumerate(number):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def detected_brand(number):
    if number.startswith("4"):
        return "VISA"
    if number[:2] in {str(value) for value in range(51, 56)} or 2221 <= int(number[:4]) <= 2720:
        return "MC"
    if number.startswith(("34", "37")):
        return "AMEX"
    return ""


class CardForm(forms.ModelForm):
    pan = forms.CharField(label="Número de tarjeta", min_length=13, max_length=23, widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric"}))
    expiry = forms.CharField(label="Vencimiento (MM/AA)", max_length=5, widget=forms.TextInput(attrs={"placeholder": "MM/AA", "autocomplete": "off"}))
    company = forms.CharField(label="Emp.", min_length=2, max_length=160, widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Empresa asociada"}))

    class Meta:
        model = PaymentCard
        fields = ["client_name", "cardholder_name", "brand", "purpose", "active"]
        labels = {"client_name": "Alias", "cardholder_name": "Titular", "brand": "Franquicia", "purpose": "Referencia", "active": "Tarjeta activa"}

    def clean_pan(self):
        digits = "".join(character for character in self.cleaned_data["pan"] if character.isdigit())
        if len(digits) not in range(13, 20) or not luhn_valid(digits):
            raise forms.ValidationError("El número no supera la validación requerida.")
        if PaymentCard.objects.filter(pan_fingerprint=fingerprint(digits)).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("La tarjeta ya está registrada.")
        return digits

    def clean_expiry(self):
        value = self.cleaned_data["expiry"]
        if len(value) != 5 or value[2] != "/" or not (value[:2] + value[3:]).isdigit() or not 1 <= int(value[:2]) <= 12:
            raise forms.ValidationError("Use formato MM/AA.")
        return value

    def clean(self):
        cleaned = super().clean()
        pan = cleaned.get("pan")
        brand = cleaned.get("brand")
        if pan and brand and detected_brand(pan) != brand:
            self.add_error("brand", "La franquicia no coincide con el número suministrado.")
        return cleaned

    def save(self, commit=True, user=None):
        obj = super().save(False)
        obj.set_pan(self.cleaned_data["pan"])
        obj.set_expiry(self.cleaned_data["expiry"])
        obj.set_company(self.cleaned_data["company"])
        if user:
            if not obj.pk:
                obj.created_by = user
            obj.updated_by = user
        if commit:
            obj.save()
        return obj


class CardEditForm(forms.ModelForm):
    pan = forms.CharField(label="Número de tarjeta", required=False, min_length=13, max_length=23, widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric", "placeholder": "Ingrese el nuevo número"}), help_text="Déjelo vacío para conservar el número actual.")
    expiry = forms.CharField(label="Vencimiento", required=False, max_length=5, widget=forms.TextInput(attrs={"placeholder": "MM/AA", "autocomplete": "off"}), help_text="Déjelo vacío para conservar el vencimiento actual.")
    company = forms.CharField(label="Emp.", required=False, min_length=2, max_length=160, widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Empresa asociada"}), help_text="Déjelo vacío para conservar la empresa actual.")
    class Meta:
        model = PaymentCard
        fields = ["client_name", "cardholder_name", "brand", "purpose"]
        labels = {"client_name": "Alias", "cardholder_name": "Titular", "brand": "Franquicia", "purpose": "Referencia"}

    def clean_pan(self):
        value = self.cleaned_data.get("pan", "")
        if not value:
            return ""
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) not in range(13, 20) or not luhn_valid(digits):
            raise forms.ValidationError("El número no supera la validación requerida.")
        if PaymentCard.objects.filter(pan_fingerprint=fingerprint(digits)).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("La tarjeta ya está registrada.")
        return digits

    def clean_expiry(self):
        value = self.cleaned_data.get("expiry", "")
        if not value:
            return ""
        if len(value) != 5 or value[2] != "/" or not (value[:2] + value[3:]).isdigit() or not 1 <= int(value[:2]) <= 12:
            raise forms.ValidationError("Use formato MM/AA.")
        return value

    def clean(self):
        cleaned = super().clean()
        brand = cleaned.get("brand")
        pan = cleaned.get("pan")
        comparison_pan = pan
        if not comparison_pan and self.instance.pk:
            comparison_pan = self.instance.get_pan()
        if comparison_pan and brand and detected_brand(comparison_pan) != brand:
            self.add_error("brand", "La franquicia no coincide con el número suministrado.")
        return cleaned

    def save(self, commit=True, user=None):
        obj = super().save(False)
        pan = self.cleaned_data.get("pan", "")
        expiry = self.cleaned_data.get("expiry", "")
        company = self.cleaned_data.get("company", "").strip()
        if pan:
            obj.set_pan(pan)
        if expiry:
            obj.set_expiry(expiry)
        if company:
            obj.set_company(company)
        if user:
            obj.updated_by = user
        if commit:
            obj.save()
        return obj


class CardSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=80, strip=True)

    def clean_q(self):
        value = self.cleaned_data.get("q", "")
        if re.search(r"(?<!\d)\d{13,19}(?!\d)", value):
            raise forms.ValidationError("Busque únicamente por referencia o últimos cuatro dígitos.")
        if value and not re.fullmatch(r"[\w\sáéíóúüñÁÉÍÓÚÜÑ.#&()'/-]+", value):
            raise forms.ValidationError("La búsqueda contiene caracteres no permitidos.")
        return value


class EmailTestForm(forms.Form):
    recipient = forms.EmailField(
        label="Destinatario de la prueba",
        max_length=254,
        help_text="Use una dirección corporativa autorizada. La prueba no incluye datos operativos.",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "administrador@ays.com.co"}),
    )


PROTECTED_FIELD_CHOICES = [("company", "Empresa"), ("pan", "Número de tarjeta"), ("expiry", "Vencimiento")]


class ProtectedActionForm(forms.Form):
    field = forms.ChoiceField(choices=PROTECTED_FIELD_CHOICES)
    action = forms.ChoiceField(choices=[("reveal", "Revelar"), ("copy", "Copiar")])


class RevealForm(ProtectedActionForm):
    """Alias compatible para el endpoint protegido existente."""


class PasswordLoginForm(forms.Form):
    username = forms.CharField(max_length=150, label="Usuario")
    password = forms.CharField(widget=forms.PasswordInput, label="Contraseña")

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs); self.request = request; self.user = None

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("username") and cleaned.get("password"):
            self.user = authenticate(self.request, username=cleaned["username"], password=cleaned["password"])
            if not self.user:
                raise forms.ValidationError("No fue posible validar las credenciales.")
        return cleaned


class OTPVerificationForm(forms.Form):
    token = forms.CharField(max_length=12, required=False, label="Código de autenticación", widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))
    recovery_code = forms.CharField(max_length=20, required=False, label="Código de recuperación", widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def clean(self):
        cleaned = super().clean()
        if bool(cleaned.get("token")) == bool(cleaned.get("recovery_code")):
            raise forms.ValidationError("Ingrese un código de autenticación o uno de recuperación.")
        return cleaned


class MFAEnrollmentForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label="Contraseña")
    token = forms.CharField(max_length=12, label="Primer código TOTP", widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))


class ReauthenticationForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Ingrese su contraseña"}), label="Contraseña")
    token = forms.CharField(min_length=6, max_length=6, label="Código de verificación", widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "placeholder": "Código de 6 dígitos", "maxlength": "6"}))


class OperationContextForm(forms.Form):
    reason = forms.CharField(label="Motivo", min_length=5, max_length=240, strip=True, widget=forms.TextInput(attrs={"placeholder": "Pago renovación empresa A&S", "autocomplete": "off"}))
    reference = forms.CharField(label="Referencia interna", min_length=3, max_length=120, strip=True, widget=forms.TextInput(attrs={"placeholder": "Póliza # 123456486789", "autocomplete": "off"}))

    def clean(self):
        cleaned = super().clean()
        for field in ("reason", "reference"):
            value = cleaned.get(field, "")
            if re.search(r"(?<!\d)\d{13,19}(?!\d)", value):
                self.add_error(field, "No incluya números completos de tarjeta en este campo.")
            if re.search(r"(?<!\d)(0[1-9]|1[0-2])[/\-]\d{2,4}(?!\d)", value):
                self.add_error(field, "No incluya fechas de vencimiento en este campo.")
        return cleaned


class ReasonForm(forms.Form):
    reason = forms.CharField(min_length=5, max_length=240, label="Motivo obligatorio")


class PolicyConfigurationForm(forms.ModelForm):
    new_session_policy = forms.ChoiceField(label="Comportamiento al iniciar sesión desde otro dispositivo", choices=[("REVOKE_PREVIOUS", "Revocar la sesión anterior"), ("BLOCK_NEW", "Bloquear la nueva sesión"), ("ALLOW_LIMIT", "Permitir según el límite configurado")])
    outside_hours_behavior = forms.ChoiceField(label="Acceso fuera del horario laboral", choices=[("ALLOW", "Permitir"), ("ALLOW_ALERT", "Permitir y generar alerta"), ("REAUTH", "Exigir reautenticación"), ("BLOCK", "Bloquear")], help_text="Define qué debe hacer A&S Vault cuando un usuario intenta ingresar o realizar una operación por fuera del horario configurado.")
    reauthentication_operations = forms.MultipleChoiceField(
        label="Operaciones que requieren reautenticación",
        choices=REAUTHENTICATION_OPERATION_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Seleccione las operaciones sensibles que deben solicitar una validación reciente.",
    )
    reason = forms.CharField(min_length=5, max_length=240, label="Motivo obligatorio")

    class Meta:
        model = PolicyConfiguration
        exclude = ["singleton", "updated_by", "updated_at"]
        labels = {
            "timezone_name": "Zona horaria", "weekday_start": "Inicio de jornada, lunes a viernes",
            "weekday_end": "Fin de jornada, lunes a viernes", "saturday_enabled": "Habilitar jornada los sábados",
            "saturday_start": "Inicio de jornada del sábado", "saturday_end": "Fin de jornada del sábado",
            "sunday_enabled": "Permitir acceso los domingos", "session_inactivity_minutes": "Cerrar sesión por inactividad, minutos",
            "maximum_sessions": "Sesiones simultáneas permitidas", "new_session_policy": "Comportamiento al iniciar sesión desde otro dispositivo",
            "reauthentication_minutes": "Vigencia de la reautenticación, minutos", "outside_hours_behavior": "Acceso fuera del horario laboral",
            "inactivity_login_days": "Días sin iniciar sesión", "inactivity_reveal_days": "Días sin consultar tarjetas",
            "inactivity_copy_days": "Días sin copiar información", "inactivity_general_days": "Días sin actividad del sistema",
            "inactive_user_days": "Días para considerar un usuario inactivo", "operational_user_days": "Días sin actividad de usuarios operativos",
            "alert_review_hours": "Tiempo máximo para revisar una alerta, horas", "escalation_hours": "Escalar alerta después de, horas",
            "enabled": "Activar esta política",
        }
        help_texts = {
            "timezone_name": "Todos los horarios del sistema se calculan con esta zona.",
            "outside_hours_behavior": "Define qué debe hacer A&S Vault cuando un usuario intenta ingresar o realizar una operación por fuera del horario configurado.",
        }
        widgets = {
            "weekday_start": forms.TimeInput(attrs={"type": "time"}), "weekday_end": forms.TimeInput(attrs={"type": "time"}),
            "saturday_start": forms.TimeInput(attrs={"type": "time"}), "saturday_end": forms.TimeInput(attrs={"type": "time"}),
        }


class AccessExceptionForm(forms.ModelForm):
    operations = forms.MultipleChoiceField(label="Operaciones permitidas", choices=ACCESS_OPERATION_CHOICES, required=False, widget=forms.CheckboxSelectMultiple)
    class Meta:
        model = AccessException
        fields = ["name", "exception_type", "user", "role", "starts_at", "ends_at", "daily_start", "daily_end", "operations", "reason"]
        labels = {"name": "Nombre", "exception_type": "Tipo de excepción", "user": "Usuario", "role": "Rol", "starts_at": "Fecha y hora inicial", "ends_at": "Fecha y hora final", "daily_start": "Hora inicial diaria", "daily_end": "Hora final diaria", "reason": "Motivo"}
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}), "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}), "daily_start": forms.TimeInput(attrs={"type": "time"}), "daily_end": forms.TimeInput(attrs={"type": "time"})}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("starts_at") and cleaned.get("ends_at") and cleaned["ends_at"] <= cleaned["starts_at"]:
            self.add_error("ends_at", "La fecha final debe ser posterior a la inicial.")
        if cleaned.get("user") and cleaned.get("role"):
            raise forms.ValidationError("Seleccione usuario o rol, no ambos.")
        return cleaned


class NotificationRecipientForm(forms.ModelForm):
    alert_types = forms.MultipleChoiceField(label="Tipos de alerta", choices=ALERT_TYPE_CHOICES, required=False, widget=forms.CheckboxSelectMultiple, help_text="Seleccione qué alertas debe recibir este destinatario.")
    class Meta:
        model = NotificationRecipient
        exclude = ["updated_by", "updated_at"]
        labels = {"name": "Nombre", "email": "Correo electrónico", "minimum_severity": "Severidad mínima", "send_start": "Hora inicial de envío", "send_end": "Hora final de envío", "delivery_mode": "Forma de entrega", "active": "Destinatario activo", "is_primary": "Correo principal", "is_leader": "Correo del líder", "is_alternate": "Correo alterno", "is_escalation": "Correo de escalamiento"}
        widgets = {"send_start": forms.TimeInput(attrs={"type": "time"}), "send_end": forms.TimeInput(attrs={"type": "time"})}


class HolidayForm(forms.ModelForm):
    reason = forms.CharField(min_length=5, max_length=240, label="Motivo obligatorio")

    class Meta:
        model = Holiday
        fields = ["date", "name", "national", "internal", "working_day"]
        labels = {"date": "Fecha", "name": "Nombre", "national": "Festivo nacional", "internal": "Festivo interno", "working_day": "Tratar como día laborable"}
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class TimelineFilterForm(forms.Form):
    PERIODS = [("", "Personalizado"), ("today", "Hoy"), ("7d", "Últimos 7 días"), ("30d", "Últimos 30 días"), ("month", "Este mes"), ("previous_month", "Mes anterior")]
    RESULTS = [("", "Todos"), ("SUCCESS", "Exitoso"), ("FAILED", "Fallido"), ("DENIED", "Denegado"), ("BLOCKED", "Bloqueado")]
    SCHEDULES = [("", "Cualquier horario"), ("inside", "Dentro del horario"), ("outside", "Fuera del horario")]
    SIZES = [("25", "25 filas"), ("50", "50 filas"), ("100", "100 filas")]
    ORDERS = [("desc", "Más recientes primero"), ("asc", "Más antiguos primero")]

    date_from = forms.DateField(label="Fecha inicial", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="Fecha final", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    user = forms.ModelChoiceField(label="Usuario", required=False, queryset=get_user_model().objects.none(), empty_label="Todos los usuarios")
    event_type = forms.ChoiceField(label="Tipo de evento", required=False, choices=[("", "Todos los eventos")] + list(AuditEvent.ACTIONS))
    role = forms.ChoiceField(label="Rol", required=False, choices=[("", "Todos los roles"), ("ADMIN", "Administrador"), ("LEADER", "Líder de cartera"), ("ANALYST", "Analista")])
    severity = forms.ChoiceField(label="Severidad", required=False, choices=[("", "Todas"), ("LOW", "Baja"), ("MEDIUM", "Media"), ("HIGH", "Alta"), ("CRITICAL", "Crítica")])
    result = forms.ChoiceField(label="Resultado", required=False, choices=RESULTS)
    schedule = forms.ChoiceField(label="Horario", required=False, choices=SCHEDULES)
    device = forms.CharField(label="Dispositivo", required=False, max_length=80)
    ip = forms.CharField(label="Dirección IP", required=False, max_length=45, help_text="Puede usar una IP completa o un prefijo seguro.")
    card = forms.CharField(label="Tarjeta", required=False, max_length=12, help_text="Solo ID interno o últimos cuatro dígitos.")
    alert = forms.IntegerField(label="Alerta relacionada", required=False, min_value=1)
    method = forms.ChoiceField(label="Método HTTP", required=False, choices=[("", "Cualquiera"), ("GET", "GET"), ("POST", "POST")])
    path = forms.CharField(label="Ruta", required=False, max_length=120)
    device_type = forms.CharField(label="Tipo de dispositivo", required=False, max_length=40)
    browser = forms.CharField(label="Navegador", required=False, max_length=50)
    operating_system = forms.CharField(label="Sistema operativo", required=False, max_length=50)
    session = forms.CharField(label="Sesión", required=False, max_length=64, help_text="Identificador técnico ya protegido.")
    alert_status = forms.ChoiceField(label="Estado de alerta", required=False, choices=[("", "Cualquiera"), ("NEW", "Nueva"), ("IN_REVIEW", "En revisión"), ("JUSTIFIED", "Justificada"), ("ESCALATED", "Escalada"), ("CLOSED", "Cerrada"), ("REOPENED", "Reabierta")])
    policy = forms.IntegerField(label="Política relacionada", required=False, min_value=1)
    exception = forms.IntegerField(label="Excepción relacionada", required=False, min_value=1)
    sensitive_only = forms.BooleanField(label="Solo operaciones sensibles", required=False)
    with_alert = forms.BooleanField(label="Solo eventos con alerta", required=False)
    failed_only = forms.BooleanField(label="Solo eventos fallidos", required=False)
    critical_only = forms.BooleanField(label="Solo eventos críticos", required=False)
    period = forms.ChoiceField(required=False, choices=PERIODS, widget=forms.HiddenInput)
    quick_event = forms.CharField(required=False, max_length=24, widget=forms.HiddenInput)
    advanced = forms.BooleanField(required=False, widget=forms.HiddenInput)
    view = forms.ChoiceField(label="Vista", required=False, choices=[("compact", "Compacta"), ("detail", "Detallada")])
    order = forms.ChoiceField(label="Orden", required=False, choices=ORDERS)
    page_size = forms.ChoiceField(label="Filas", required=False, choices=SIZES)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user and getattr(user, "vault_profile", None) and user.vault_profile.role != "ANALYST":
            self.fields["user"].queryset = get_user_model().objects.filter(vault_profile__active=True).select_related("vault_profile").order_by("username")

    def clean_ip(self):
        value = self.cleaned_data.get("ip", "").strip()
        if not value:
            return ""
        if not re.fullmatch(r"[0-9a-fA-F:.]{2,45}", value):
            raise forms.ValidationError("Ingrese una dirección IP válida o un prefijo numérico seguro.")
        if len(value) >= 7:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                if not (value.endswith(".") or value.endswith(":")):
                    raise forms.ValidationError("La dirección IP no tiene un formato válido.")
        return value

    def clean_card(self):
        value = self.cleaned_data.get("card", "").strip()
        if not value:
            return ""
        if not value.isdigit() or len(value) > 12:
            raise forms.ValidationError("Use únicamente el ID interno o los últimos cuatro dígitos.")
        if 13 <= len(value) <= 19:
            raise forms.ValidationError("No está permitido buscar por número completo de tarjeta.")
        return value

    def clean(self):
        cleaned = super().clean()
        today = timezone.localdate()
        period = cleaned.get("period")
        if period == "today": cleaned["date_from"] = cleaned["date_to"] = today
        elif period == "7d": cleaned["date_from"], cleaned["date_to"] = today - timedelta(days=6), today
        elif period == "30d": cleaned["date_from"], cleaned["date_to"] = today - timedelta(days=29), today
        elif period == "month": cleaned["date_from"], cleaned["date_to"] = today.replace(day=1), today
        elif period == "previous_month":
            end = today.replace(day=1) - timedelta(days=1); cleaned["date_from"], cleaned["date_to"] = end.replace(day=1), end
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "La fecha final debe ser igual o posterior a la fecha inicial.")
        if start and end and (end - start).days > settings.REPORT_DEFAULT_MAX_DAYS:
            self.add_error("date_to", f"El rango no puede superar {settings.REPORT_DEFAULT_MAX_DAYS} días sin autorización ampliada.")
        if self.user and getattr(self.user, "vault_profile", None) and self.user.vault_profile.role == "ANALYST":
            cleaned["user"] = self.user
        return cleaned


class ReportRequestForm(forms.Form):
    report_type = forms.ChoiceField(choices=[])
    export_format = forms.ChoiceField(choices=[("XLSX", "Excel"), ("PDF", "PDF")])
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)
    orientation = forms.ChoiceField(required=False, choices=[("auto", "Automática"), ("portrait", "Vertical"), ("landscape", "Horizontal")])
    detail = forms.ChoiceField(required=False, choices=[("summary", "Resumen"), ("detail", "Detallado")])

    def __init__(self, *args, allowed_types=(), **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(ReportExport.TYPES)
        self.fields["report_type"].choices = [(value, labels[value]) for value in allowed_types]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "La fecha final debe ser igual o posterior a la inicial.")
        if start and end and (end - start).days > settings.REPORT_DEFAULT_MAX_DAYS:
            self.add_error("date_to", f"El rango no puede superar {settings.REPORT_DEFAULT_MAX_DAYS} días.")
        return cleaned
