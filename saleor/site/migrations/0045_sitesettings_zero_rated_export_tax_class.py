import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("site", "0044_sitesettings_invoice_product_code_attribute"),
        ("tax", "0012_taxclasscountryrate_xero_tax_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="zero_rated_export_tax_class",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="tax.taxclass",
            ),
        ),
    ]
