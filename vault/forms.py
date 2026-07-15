from django import forms
from django.contrib.auth import authenticate

from .crypto import fingerprint
from .models import AccessException, Holiday, NotificationRecipient, PaymentCard, PolicyConfiguration


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

    class Meta:
        model = PaymentCard
        fields = ["client_name", "cardholder_name", "brand", "purpose", "active"]

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
        if user:
            if not obj.pk:
                obj.created_by = user
            obj.updated_by = user
        if commit:
            obj.save()
        return obj


class CardEditForm(forms.ModelForm):
    class Meta:
        model = PaymentCard
        fields = ["client_name", "cardholder_name", "brand", "purpose"]

    def save(self, commit=True, user=None):
        obj = super().save(False)
        if user:
            obj.updated_by = user
        if commit:
            obj.save()
        return obj


class RevealForm(forms.Form):
    field = forms.ChoiceField(choices=[("pan", "Número de tarjeta"), ("expiry", "Vencimiento")])
    reason = forms.CharField(max_length=240, min_length=5)
    reference = forms.CharField(max_length=120, required=False)
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)


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
    password = forms.CharField(widget=forms.PasswordInput, label="Contraseña")
    token = forms.CharField(max_length=12, label="Código TOTP", widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))


class ReasonForm(forms.Form):
    reason = forms.CharField(min_length=5, max_length=240, label="Motivo obligatorio")


class PolicyConfigurationForm(forms.ModelForm):
    reason = forms.CharField(min_length=5, max_length=240, label="Motivo obligatorio")

    class Meta:
        model = PolicyConfiguration
        exclude = ["singleton", "updated_by", "updated_at"]
        widgets = {"reauthentication_operations": forms.Textarea(attrs={"rows": 3})}


class AccessExceptionForm(forms.ModelForm):
    class Meta:
        model = AccessException
        fields = ["name", "exception_type", "user", "role", "starts_at", "ends_at", "daily_start", "daily_end", "operations", "reason"]
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}), "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}), "operations": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("starts_at") and cleaned.get("ends_at") and cleaned["ends_at"] <= cleaned["starts_at"]:
            self.add_error("ends_at", "La fecha final debe ser posterior a la inicial.")
        if cleaned.get("user") and cleaned.get("role"):
            raise forms.ValidationError("Seleccione usuario o rol, no ambos.")
        return cleaned


class NotificationRecipientForm(forms.ModelForm):
    reason = forms.CharField(min_length=5, max_length=240, label="Motivo obligatorio")

    class Meta:
        model = NotificationRecipient
        exclude = ["updated_by", "updated_at"]


class HolidayForm(forms.ModelForm):
    reason = forms.CharField(min_length=5, max_length=240, label="Motivo obligatorio")

    class Meta:
        model = Holiday
        fields = ["date", "name", "national", "internal", "working_day"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
