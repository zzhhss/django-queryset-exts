import json

import django
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import NOT_PROVIDED
from django.db.models.query_utils import DeferredAttribute
from django.utils.functional import curry


class APIUUIDDescriptor(DeferredAttribute):

    def __init__(self, field_name, model, single_method=None, batch_method=None, remote_uuid_getter=None, local_uuid_getter=None):
        if django.VERSION < (2, 0):
            super().__init__(field_name, None)
        else:
            super().__init__(field_name)
        self.single_method = single_method
        self.batch_method = batch_method
        self.remote_uuid_getter = (remote_uuid_getter or (lambda d: d.get('uuid')))
        self.local_uuid_getter = (local_uuid_getter or (lambda d: d.get('uuid')))

    @property
    def cache_name(self):
        return self.field_name

    def set_cache(self, instance, val):
        if not hasattr(instance, '_api_related_cache'):
            setattr(instance, '_api_related_cache', {})
        getattr(instance, '_api_related_cache')[self.cache_name] = val

    def is_cached(self, instance):
        return self.cache_name in getattr(instance, '_api_related_cache', {}).keys()

    def get_cache_value(self, instance):
        return getattr(instance, '_api_related_cache', {}).get(self.cache_name)

    def get_value(self, instance):
        if self.is_cached(instance):
            return self.get_cache_value(instance)
        rel_qs, rel_obj_attr, _ = self.get_related_api_objects([instance])
        for obj in rel_qs:
            rel_obj_attr(obj)
        if isinstance(self.get_local_attr_value(instance), list):
            value = rel_qs
        else:
            value = rel_qs[0] if rel_qs else None
        self.set_cache(instance, value)
        return value

    def get_local_attr_value(self, instance):
        value = getattr(instance, self.field_name)
        # value supports those formats:
        # 1. string represent an ID
        # 2. IDs joined with ,
        # 2. list of dict
        if isinstance(value, list):
            if value:
                one_value = value[0]
                if isinstance(one_value, dict):
                    return [self.local_uuid_getter(one) for one in value]
                else:
                    return value
            return []
        elif isinstance(value, dict):
            pass
        else:
            return value

    @property
    def temp_identifier_name(self):
        return '_' + self.field_name + '_identifier'

    def get_related_api_objects(self, instances):
        uuids = set()
        for instance in instances:
            value = self.get_local_attr_value(instance)
            if isinstance(value, list):
                uuids.update(value)
            else:
                uuids.add(value)
        uuids = list(uuids)

        if self.batch_method:
            data = self.batch_method(uuids)
            for d in data:
                d[self.temp_identifier_name] = self.remote_uuid_getter(d)  # TODO: refactor
        else:
            data = []
            for uuid in uuids:
                d = self.single_method(uuid)
                d[self.temp_identifier_name] = uuid
                data.append(d)
        return data, lambda x: x.pop(self.temp_identifier_name, None), self.get_local_attr_value


class APIUUIDDataDescriptor:

    def __init__(self, field_name, model, cls_descriptor: APIUUIDDescriptor):
        self.field_name = field_name
        self.cls_descriptor = cls_descriptor  # type: APIUUIDDescriptor

    def __get__(self, instance, owner):
        if not instance:
            return self
        value = self.cls_descriptor.get_value(instance)
        return value

    def __set__(self, instance, value):
        if not instance:
            pass
        self.cls_descriptor.set_cache(instance, value)


class RemoteUUIDFieldMixin:
    def __init__(self, *args, single_method=None, batch_method=None, local_uuid_getter=None, remote_uuid_getter=None, **kwargs):

        # TODO: do validate here
        self.single_method = single_method
        self.batch_method = batch_method
        self.local_uuid_getter = local_uuid_getter
        self.remote_uuid_getter = remote_uuid_getter
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs['single_method'] = self.single_method
        kwargs['batch_method'] = self.batch_method
        kwargs['local_uuid_getter'] = self.local_uuid_getter
        kwargs['remote_uuid_getter'] = self.remote_uuid_getter
        return name, path, args, kwargs

    def contribute_to_class(self, cls, name, private_only=False, virtual_only=NOT_PROVIDED):
        """
                Register the field with the model class it belongs to.

                If private_only is True, a separate instance of this field will be
                created for every subclass of cls, even if cls is not an abstract
                model.
                """
        if virtual_only is not NOT_PROVIDED:
            private_only = virtual_only
        self.set_attributes_from_name(name)
        self.model = cls
        if private_only:
            cls._meta.add_field(self, private=True)
        else:
            cls._meta.add_field(self)
        if self.column:
            # Don't override classmethods with the descriptor. This means that
            # if you have a classmethod and a field with the same name, then
            # such fields can't be deferred (we don't have a check for this).
            if not getattr(cls, self.attname, None):
                cls_descriptor = APIUUIDDescriptor(self.attname, cls, single_method=self.single_method,
                                                   batch_method=self.batch_method,
                                                   local_uuid_getter=self.local_uuid_getter, remote_uuid_getter=self.remote_uuid_getter)
                setattr(cls, self.attname, cls_descriptor)
                setattr(cls, '%s_data' % self.name,
                        APIUUIDDataDescriptor(self.attname, cls, cls_descriptor=cls_descriptor))
        if self.choices:
            setattr(cls, 'get_%s_display' % self.name,
                    curry(cls._get_FIELD_display, field=self))


class ListFieldMixin:
    if django.VERSION < (2, 0):
        def from_db_value(self, value, expression, connection, context):
            return self.to_python(value)
    else:
        def from_db_value(self, value, expression, connection):
            return self.to_python(value)

    def to_python(self, value):
        if not value:
            return []
        if isinstance(value, str):
            if value:
                return value.split(',')
            return []
        if isinstance(value, list):
            return value
        raise ValidationError('Invalid value for {}\'s {}.'.format(self.model.__name__, self.name))

    def get_prep_value(self, value):
        if value is None:
            return None
        if not value:
            return ''
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return ','.join(value)
        raise ValidationError('Invalid value for {}\'s {}.'.format(self.model.__name__, self.name))


class RemoteUUIDField(RemoteUUIDFieldMixin, models.CharField):
    pass


class RemoteUUIDListField(ListFieldMixin, RemoteUUIDFieldMixin, models.CharField):
    pass


class RemoteUUIDLargeListField(ListFieldMixin, RemoteUUIDFieldMixin, models.TextField):
    pass


class RemoteUUIDListJSONField(RemoteUUIDFieldMixin, models.TextField):
    if django.VERSION < (2, 0):
        def from_db_value(self, value, expression, connection, context):
            return self.to_python(value)
    else:
        def from_db_value(self, value, expression, connection):
            return self.to_python(value)

    def to_python(self, value):
        if not value:
            return []
        if isinstance(value, str):
            if value:
                try:
                    return json.loads(value)
                except Exception as e:
                    _ = e
                    return []
            return []
        if isinstance(value, list):
            return value
        raise ValidationError('Invalid value for {}\'s {}.'.format(self.model.__name__, self.name))

    def get_prep_value(self, value):
        if not value:
            return '[]'
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return json.dumps(value)
        raise ValidationError('Invalid value for {}\'s {}.'.format(self.model.__name__, self.name))
