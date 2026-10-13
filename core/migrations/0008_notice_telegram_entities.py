from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_trusted_browser"),
    ]

    operations = [
        migrations.AddField(
            model_name="notice",
            name="telegram_entities",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
