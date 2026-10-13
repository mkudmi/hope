from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_notice_telegram_entities"),
    ]

    operations = [
        migrations.AddField(
            model_name="notice",
            name="telegram_account_menu",
            field=models.BooleanField(default=False),
        ),
    ]
