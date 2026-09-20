import uuid
from django.db import models


# the only table in the db
class BankRecord(models.Model):

    # the columns I add to the data
    run_id = models.UUIDField(default=uuid.uuid4, db_index=True) # uuid -> Universally Unique Identifiers (128-bit)
    timestamp = models.DateTimeField(auto_now_add=True)

    # the columns from data
    bank_code = models.CharField(max_length=50)
    period = models.CharField(max_length=20)
    account_code = models.CharField(max_length=50)

    # null - > DB allows NuLL blank -> forms/admin allow empty
    debit = models.CharField(max_length=50, blank=True, default="")
    credit = models.CharField(max_length=50, blank=True, default="")
    balance = models.CharField(max_length=50, blank=True, default="")

    valid = models.BooleanField(default=False) # sqlite stores it as 0 , 1
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-timestamp"] # default order of query results
        indexes = [models.Index(fields=["run_id", "valid"])] # create composite index on two columns

    def __str__(self):
        return f"{self.run_id} | {self.bank_code} | {self.account_code}"



class ValidationRun(models.Model):
    run_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    file_name = models.CharField(max_length=255, blank=True, default="")
    total = models.IntegerField(default=0)
    valid_count = models.IntegerField(default=0)
    invalid_count = models.IntegerField(default=0)
    errors_by_code = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.run_id} | {self.file_name} | {self.total}"