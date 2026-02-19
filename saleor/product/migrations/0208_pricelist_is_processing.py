from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "product",
            "0207_rename_product_pri_price_l_is_valid_idx_product_pri_price_l_2a35bf_idx",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="pricelist",
            name="is_processing",
            field=models.BooleanField(default=False),
        ),
    ]
