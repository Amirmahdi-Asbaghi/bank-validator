import uuid
from django.db import models


# the only table in the db
class BankRecord(models.Model):
    # defines the allowed input modes for records
    # I use nested class for better scope
    # source type only meaning 
    # "BankRecord.SourceType.JSON_FILE" better understanding access for later use

    class SourceType(models.TextChoices):

        # TextChoices is Django's helper for defining a fixed set of choices,
        # pairing each stored value with a human-readable label 
        # the first value stores in db and the second one is human-readable shown in admin and api's
        JSON_FILE = "json_file", "JSON File" # (value, label)
        CSV_FILE = "csv_file", "CSV File"

    # the columns I add to the data
    run_id = models.UUIDField(default=uuid.uuid4, db_index=True) # uuid -> Universally Unique Identifiers (128-bit)
    timestamp = models.DateTimeField(auto_now_add=True)
    source_type = models.CharField(max_length=20, choices=SourceType)
    file_name = models.CharField(max_length=255, blank=True, default="")

    # the columns from data
    bank_code = models.CharField(max_length=50)
    period = models.CharField(max_length=20)
    account_code = models.CharField(max_length=50)

    # null - > DB allows NuLL blank -> forms/admin allow empty
    debit = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    credit = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    balance = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)

    valid = models.BooleanField(default=False) # sqlite stores it as 0 , 1
    errors = models.CharField(max_length=70, blank=True, default="") # max 16 errors E00...E015 and some extra

    class Meta:
        ordering = ["-timestamp"] # default order of query results
        indexes = [models.Index(fields=["run_id", "valid"])] # create composite index on two columns

    def __str__(self):
        return f"{self.run_id} | {self.bank_code} | {self.account_code}"