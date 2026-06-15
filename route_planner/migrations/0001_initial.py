import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="FuelStation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("opis_id", models.IntegerField(db_index=True, unique=True)),
                ("name", models.CharField(max_length=200)),
                ("address", models.CharField(blank=True, max_length=500)),
                ("city", models.CharField(db_index=True, max_length=100)),
                ("state", models.CharField(db_index=True, max_length=10)),
                ("retail_price", models.DecimalField(decimal_places=6, max_digits=8)),
                ("lat", models.FloatField(blank=True, db_index=True, null=True)),
                ("lon", models.FloatField(blank=True, null=True)),
                ("geocoded", models.BooleanField(db_index=True, default=False)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["lat", "lon"], name="route_plane_lat_lon_idx"),
                    models.Index(fields=["state", "city"], name="route_plane_state_city_idx"),
                ],
            },
        ),
    ]
