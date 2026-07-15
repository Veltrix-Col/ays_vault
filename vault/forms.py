from django import forms
from .models import PaymentCard
class CardForm(forms.ModelForm):
    pan=forms.CharField(label='Número de tarjeta',min_length=13,max_length=23,widget=forms.TextInput(attrs={'autocomplete':'off','inputmode':'numeric'}))
    expiry=forms.CharField(label='Vencimiento (MM/AA)',max_length=5,widget=forms.TextInput(attrs={'placeholder':'MM/AA','autocomplete':'off'}))
    class Meta: model=PaymentCard; fields=['client_name','cardholder_name','brand','purpose','active']
    def clean_pan(self):
        digits=''.join(c for c in self.cleaned_data['pan'] if c.isdigit())
        if len(digits) not in range(13,20): raise forms.ValidationError('Debe contener entre 13 y 19 dígitos.')
        return digits
    def clean_expiry(self):
        v=self.cleaned_data['expiry']
        if len(v)!=5 or v[2]!='/' or not(v[:2]+v[3:]).isdigit() or not 1<=int(v[:2])<=12: raise forms.ValidationError('Use formato MM/AA.')
        return v
    def save(self,commit=True,user=None):
        obj=super().save(False); obj.set_pan(self.cleaned_data['pan']); obj.set_expiry(self.cleaned_data['expiry'])
        if user and not obj.pk: obj.created_by=user
        if commit: obj.save()
        return obj
class RevealForm(forms.Form):
    field=forms.ChoiceField(choices=[('pan','Número de tarjeta'),('expiry','Vencimiento')]); reason=forms.CharField(max_length=240); password=forms.CharField(widget=forms.PasswordInput)
    def __init__(self,*args,user=None,**kwargs): super().__init__(*args,**kwargs); self.user=user
    def clean_password(self):
        p=self.cleaned_data['password']
        if not self.user.check_password(p): raise forms.ValidationError('Contraseña incorrecta.')
        return p
