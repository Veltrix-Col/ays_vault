from django import forms

from .crypto import fingerprint
from .models import PaymentCard


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
    password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user or not self.user.is_active or not self.user.check_password(password):
            raise forms.ValidationError("No fue posible confirmar la identidad.")
        return password
