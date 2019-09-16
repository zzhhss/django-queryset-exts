django-queryset-exts
=============
A django queryset with a select_related(or prefetch_related) like method to fetch remote(e.g., api) data.

Requirements
------------

* **Python**: 3.6, 3.7
* **Django**: 2.0, 2.1, 2.2

Installation
------------

Install using pip:

    pip install django-queryset-exts

Usage
------------

First, create your models. You can either inherit your model from ``django_queryset_exts.models.Model``, 
or write ``objects = APIQueryset.as_manager()`` in your model 

```Python
from django.db import models

from django_queryset_exts.query import APIQueryset 
from django_queryset_exts.models import Model 
from django_queryset_exts.fields import RemoteUUIDField 

# inherit from django_queryset_exts.models.Model
class MyModel(Model):
    name = models.CharField(max_length=50)
    topic = models.ForeignKey('SomeModel', on_delete=models.CASCADE)
    remote_uuid = RemoteUUIDField(max_length=30, single_method=get_uuid_detail_from_api, batch_method=get_uuids_details_from_api)
    
# if you do not want to inherit from django_queryset_exts.models.Model
class MyModel(models.Model):
    name = models.CharField(max_length=50)
    topic = models.ForeignKey('SomeModel', on_delete=models.CASCADE)
    remote_uuid = RemoteUUIDField(max_length=30, single_method=get_uuid_detail_from_api, batch_method=get_uuids_details_from_api)
    
    objects = APIQueryset.as_manager()
```

Then, implement the functions you passed to ``batch_method``(used when you retrieve list of objects) and ``single_method``(used when you retrieve single object)


```Python
def get_uuids_details_from_api(uuids: list):
    """
    this function accept a list of ids and return a list of dict (in most cases, from API) which has a field represent the id.
    """
    return [{'uuid': 'xxx', 'field1': '', 'field2': ''}]

def get_uuid_detail_from_api(uuid):
    """
    this function accept one id and return a dict (in most cases, from API) which has a field equeal to uuid.
    """
    return {'uuid': 'xxx', 'field1': '', 'field2': ''}
```

Finally, you can use ``select_api_related`` method on ``objects`` to load remote data into field ``<field_name>_data``


```Python

objs = list(MyModel.objects.select_api_related('remote_uuid'))

len(objs)
# 57 

objs[0].remote_uuid_data 
# {'uuid': 'xxx', 'field1': '', 'field2': ''}

objs[1].remote_uuid_data 
# {'uuid': 'xxx', 'field1': '', 'field2': ''}
```
The example above will totally call ``get_uuids_details_from_api`` once.