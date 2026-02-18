from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0226_add_xero_prepayment_id_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="xero_bank_account_code",
            field=models.CharField(
                blank=True,
                help_text="Xero bank account code used for deposit prepayments on this order.",
                max_length=10,
                null=True,
            ),
        ),
    ]
