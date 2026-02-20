from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0011_merge_20250530_0929"),
    ]

    operations = [
        migrations.AddField(
            model_name="taxclasscountryrate",
            name="xero_tax_code",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
