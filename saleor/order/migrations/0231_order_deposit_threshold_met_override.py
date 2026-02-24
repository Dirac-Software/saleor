from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0230_order_shipping_xero_tax_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="deposit_threshold_met_override",
            field=models.BooleanField(default=False),
        ),
    ]
