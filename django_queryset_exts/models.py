from django.db import models
from .query import APIQueryset


# this is an example.
class Model(models.Model):
    objects = APIQueryset.as_manager()
