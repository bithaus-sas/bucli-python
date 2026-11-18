from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bucli_billing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingaccount",
            name="bucli_updated_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Último cambio en Bucli"),
        ),
    ]
