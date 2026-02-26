from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0233_remove_fulfillment_quote_pdf_url"),
    ]
    operations = [
        migrations.AlterField(
            model_name="order",
            name="inco_term",
            field=models.CharField(
                blank=True,
                choices=[
                    ("EXW", "Ex Works"),
                    ("FCA", "Free Carrier"),
                    ("CPT", "Carriage Paid To"),
                    ("CIP", "Carriage and Insurance Paid To"),
                    ("DAP", "Delivered At Place"),
                    ("DPU", "Delivered at Place Unloaded"),
                    ("DDP", "Delivered Duty Paid"),
                    ("FAS", "Free Alongside Ship"),
                    ("FOB", "Free On Board"),
                    ("CFR", "Cost and Freight"),
                    ("CIF", "Cost Insurance and Freight"),
                ],
                default=None,
                max_length=3,
                null=True,
            ),
        ),
    ]
