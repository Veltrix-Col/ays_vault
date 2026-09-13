from django import forms
class IgnoreExceptionForm(forms.Form):
    reason = forms.ChoiceField(choices=(("No requiere gestión", "No requiere gestión"), ("Informativo", "Informativo"), ("Duplicado", "Duplicado"), ("Ya gestionado por otro medio", "Ya gestionado por otro medio"), ("Ruido", "Ruido"), ("Otro", "Otro")))
    comment = forms.CharField(required=False, widget=forms.Textarea)
    def clean(self):
        data = super().clean()
        if data.get("reason") == "Otro" and not data.get("comment", "").strip(): self.add_error("comment", "El comentario es obligatorio para Otro.")
        return data
class CreateTaskForm(forms.Form):
    responsible = forms.ChoiceField(choices=(), label="Responsable Zoho")
    subject = forms.CharField(max_length=255)
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    description = forms.CharField(widget=forms.Textarea)
