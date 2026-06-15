from django.db import models


class FuelStation(models.Model):
    opis_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=10, db_index=True)
    retail_price = models.DecimalField(max_digits=8, decimal_places=6)
    lat = models.FloatField(null=True, blank=True, db_index=True)
    lon = models.FloatField(null=True, blank=True)
    geocoded = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["lat", "lon"]),
            models.Index(fields=["state", "city"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state} (${self.retail_price})"
