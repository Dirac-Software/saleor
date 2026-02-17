from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0203_pricelist"),
    ]

    operations = [
        migrations.AddField(
            model_name="pricelist",
            name="name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
