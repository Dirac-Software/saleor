from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0205_pricelist_channels"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="pricelistitem",
            index=models.Index(
                fields=["price_list", "is_valid"],
                name="product_pri_price_l_is_valid_idx",
            ),
        ),
    ]
