from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0231_order_deposit_threshold_met_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="xero_bank_account_sort_code",
            field=models.CharField(
                blank=True,
                help_text="Bank sort code for the Xero bank account used for deposit prepayments.",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="xero_bank_account_number",
            field=models.CharField(
                blank=True,
                help_text="Bank account number for the Xero bank account used for deposit prepayments.",
                max_length=20,
                null=True,
            ),
        ),
    ]
