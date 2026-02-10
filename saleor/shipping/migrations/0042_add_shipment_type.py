from django.db import migrations, models


def populate_shipment_types(apps, schema_editor):
    Shipment = apps.get_model("shipping", "Shipment")
    db_alias = schema_editor.connection.alias

    for shipment in Shipment.objects.using(db_alias).all():
        if shipment.purchase_order_items.exists():
            shipment.shipment_type = "inbound"
        elif shipment.fulfillments.exists():
            shipment.shipment_type = "outbound"
        else:
            shipment.shipment_type = "inbound"
        shipment.save(update_fields=["shipment_type"])


def reverse_populate_shipment_types(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0041_add_blank_true_to_optional_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="shipment",
            name="shipment_type",
            field=models.CharField(
                choices=[
                    ("inbound", "Inbound from supplier"),
                    ("outbound", "Outbound to customer"),
                ],
                help_text="Whether this shipment is inbound (from supplier) or outbound (to customer)",
                max_length=10,
                null=True,
            ),
        ),
        migrations.RunPython(
            populate_shipment_types,
            reverse_code=reverse_populate_shipment_types,
        ),
        migrations.AlterField(
            model_name="shipment",
            name="shipment_type",
            field=models.CharField(
                choices=[
                    ("inbound", "Inbound from supplier"),
                    ("outbound", "Outbound to customer"),
                ],
                help_text="Whether this shipment is inbound (from supplier) or outbound (to customer)",
                max_length=10,
            ),
        ),
    ]
