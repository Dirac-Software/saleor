from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("channel", "0027_channel_allow_legacy_gift_card_use"),
        ("product", "0204_pricelist_add_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="pricelist",
            name="channels",
            field=models.ManyToManyField(
                blank=True,
                related_name="price_lists",
                to="channel.channel",
            ),
        ),
    ]
