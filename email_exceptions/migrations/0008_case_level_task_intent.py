from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("email_exceptions", "0007_exceptioncase_assigned_at_exceptioncase_assigned_to_and_more")]

    operations = [
        migrations.AlterField(
            model_name="caseactivity", name="event_type",
            field=models.CharField(max_length=40, choices=[
                ("CASE_CREATED", "Caso creado"), ("STATUS_CHANGED", "Estado cambiado"),
                ("RESOLVED", "Caso resuelto"), ("CLOSED", "Caso cerrado"), ("REOPENED", "Caso reabierto"),
                ("ASSIGNED", "Caso asignado"), ("REASSIGNED", "Caso reasignado"), ("UNASSIGNED", "Caso desasignado"),
                ("FOLLOW_UP_UPDATED", "Seguimiento actualizado"), ("NOTE_ADDED", "Nota interna añadida"),
                ("TASK_CREATE_REQUESTED", "Creación de tarea solicitada"), ("TASK_CREATED", "Tarea creada"),
                ("TASK_CREATE_FAILED", "Creación de tarea fallida"), ("TASK_RECONCILE_REQUIRED", "Reconciliación requerida"),
            ]),
        ),
        migrations.AlterField(
            model_name="zohotaskcreation", name="exception",
            field=models.OneToOneField(blank=True, null=True, on_delete=models.deletion.CASCADE, related_name="zoho_task", to="email_exceptions.emailexception"),
        ),
        migrations.AddField(model_name="zohotaskcreation", name="case", field=models.OneToOneField(blank=True, null=True, on_delete=models.deletion.CASCADE, related_name="zoho_task", to="email_exceptions.exceptioncase")),
        migrations.AddField(model_name="zohotaskcreation", name="responsible", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="zohotaskcreation", name="ramo", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="zohotaskcreation", name="fingerprint", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="zohotaskcreation", name="requested_by", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="email_case_task_requests", to="auth.user")),
        migrations.AddField(model_name="zohotaskcreation", name="error_category", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="zohotaskcreation", name="updated_at", field=models.DateTimeField(auto_now=True)),
        migrations.AddConstraint(model_name="zohotaskcreation", constraint=models.CheckConstraint(condition=Q(case__isnull=False) | Q(exception__isnull=False), name="email_task_has_case_or_exception")),
    ]
