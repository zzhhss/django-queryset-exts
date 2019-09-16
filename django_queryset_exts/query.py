import copy
from collections import deque

from django.core import exceptions
from django.db import models
from django.db.models.constants import LOOKUP_SEP
from django.db.models.signals import post_save
from django.utils.functional import cached_property

from .signals import pre_update, post_update


class APIQueryset(models.QuerySet):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._select_related_api_fields = ()
        self._select_related_api_done = False
        self._api_result_cache = None

    def __deepcopy__(self, memo):
        """
        Deep copy of a QuerySet doesn't populate the cache
        """
        obj = self.__class__()
        for k, v in self.__dict__.items():
            if k in ['_result_cache', '_api_result_cache']:
                obj.__dict__[k] = None
            else:
                obj.__dict__[k] = copy.deepcopy(v, memo)
        return obj

    def update(self, **kwargs):
        pre_update.send(sender=self.model, queryset=self._clone(), kwargs=kwargs)
        result = super().update(**kwargs)
        post_update.send(sender=self.model, queryset=self._clone(), kwargs=kwargs, result=result)
        return result

    def bulk_create(self, objs, batch_size=None):
        objs = super().bulk_create(objs, batch_size)
        for obj in objs:
            post_save.send(sender=self.model, instance=obj, created=getattr(self, 'pk', None),
                           update_fields=None, raw=False, using=None,)
        return objs

    def select_api_related(self, *fields):
        clone = self._clone()
        if fields == (None,):
            clone._select_related_api_fields = ()
        else:
            clone._select_related_api_fields = clone._select_related_api_fields + fields
        return clone

    def _fetch_all(self):
        super()._fetch_all()
        if self._select_related_api_fields and not self._select_related_api_done:
            self._prefetch_related_api_objects()

    def _prefetch_related_api_objects(self):
        # This method can only be called once the result cache has been filled.
        select_related_api_objects(self._result_cache, *self._select_related_api_fields)
        self._select_related_api_done = True

    def _clone(self, **kwargs) -> 'APIQueryset':
        clone = super()._clone(**kwargs)

        # add this lines
        clone._select_related_api_fields = self._select_related_api_fields
        clone._select_related_api_done = self._select_related_api_done

        return clone


class SelectAPIRelated:
    """django.Prefetch"""

    def __init__(self, lookup, to_attr=None):
        self.select_through = lookup
        self.to_attr = '{}_{}'.format(lookup, 'data') if not to_attr else to_attr  # TODO: 优化
        self.select_to = LOOKUP_SEP.join(lookup.split(LOOKUP_SEP)[:-1] + [self.to_attr])

    def __eq__(self, other):
        if isinstance(other, SelectAPIRelated):
            return self.select_to == other.select_to
        return False

    def __hash__(self):
        return hash(self.__class__) ^ hash(self.select_to)

    def get_current_select_to(self, level):
        return LOOKUP_SEP.join(self.select_to.split(LOOKUP_SEP)[:level + 1])

    def get_current_to_attr(self, level):
        parts = self.select_to.split(LOOKUP_SEP)
        to_attr = parts[level]
        as_attr = self.to_attr and level == len(parts) - 1
        return to_attr, as_attr


def normalize_api_select(lookups):
    ret = []
    for lookup in lookups:
        if not isinstance(lookup, SelectAPIRelated):
            lookup = SelectAPIRelated(lookup)
        ret.append(lookup)
    return ret


def select_related_api_objects(model_instances, *related_lookups):
    """
    Populate prefetched object caches for a list of model instances based on
    the lookups/Prefetch instances given.
    """
    if len(model_instances) == 0:
        return  # nothing to do

    # 全部用SelectAPIRelated对象包装
    related_lookups = normalize_api_select(related_lookups)

    # Need some book keeping to ensure we don't do duplicate work.
    done_queries = {}  # dictionary of things like 'foo__bar': [results]

    auto_lookups = set()  # we add to this as we go through.
    followed_descriptors = set()  # recursion protection

    all_lookups = deque(related_lookups)
    while all_lookups:
        lookup = all_lookups.popleft()
        if lookup.select_to in done_queries:
            continue

        # Top level, the list of objects to decorate is the result cache
        # from the primary QuerySet. It won't be for deeper levels.
        obj_list = model_instances

        through_attrs = lookup.select_through.split(LOOKUP_SEP)
        for level, through_attr in enumerate(through_attrs):
            # Prepare main instances
            if len(obj_list) == 0:
                break

            select_to = lookup.get_current_select_to(level)
            if select_to in done_queries:
                obj_list = done_queries[select_to]
                continue

            # Descend down tree
            first_obj = obj_list[0]
            to_attr = lookup.get_current_to_attr(level)[0]  # 用来判断 is_fetched
            something_for_select, descriptor, attr_found, is_fetched \
                = get_something_can_do_select_api(first_obj, through_attr, to_attr)

            if not attr_found:
                raise AttributeError("Cannot find '%s' on %s object, '%s' is an invalid "
                                     "parameter to select_api_related()" %
                                     (through_attr, first_obj.__class__.__name__, lookup.select_through))

            if level == len(through_attrs) - 1 and something_for_select is None:
                # Last one, this *must* resolve to something that supports
                # api_select, otherwise there is no point adding it and the
                # developer asking for it has made a mistake.
                raise ValueError("'%s' does not resolve to an item that supports "
                                 "api_select - this is an invalid parameter to "
                                 "select_api_related()." % lookup.select_through)

            if something_for_select is not None and not is_fetched:
                obj_list = select_one_level(obj_list, something_for_select, lookup, level)
                if descriptor not in followed_descriptors:
                    done_queries[select_to] = obj_list
                followed_descriptors.add(descriptor)
            else:
                new_obj_list = []
                for obj in obj_list:

                    if something_for_select:
                        this_descriptor = something_for_select
                        new_obj = this_descriptor.get_cache_value(obj)
                    elif through_attr in getattr(obj, '_prefetched_objects_cache', ()):
                        # If related objects have been prefetched, use the
                        # cache rather than the object's through_attr.
                        new_obj = list(obj._prefetched_objects_cache.get(through_attr))
                    else:
                        try:
                            new_obj = getattr(obj, through_attr)
                        except exceptions.ObjectDoesNotExist:
                            continue
                    if hasattr(new_obj, 'get_prefetch_queryset'):
                        new_obj = list(new_obj.all())
                    if new_obj is None:
                        continue
                    # We special-case `list` rather than something more generic
                    # like `Iterable` because we don't want to accidentally match
                    # user models that define __iter__.
                    if isinstance(new_obj, list):
                        new_obj_list.extend(new_obj)
                    else:
                        new_obj_list.append(new_obj)
                obj_list = new_obj_list


def get_something_can_do_select_api(instance, through_attr, to_attr):
    something = None
    is_fetched = False

    # For singly related objects, we have to avoid getting the attribute
    # from the object, as this will trigger the query. So we first try
    # on the class, in order to get the descriptor object.
    rel_obj_descriptor = getattr(instance.__class__, through_attr, None)
    if rel_obj_descriptor is None:
        attr_found = hasattr(instance, through_attr)
    else:
        attr_found = True
        if rel_obj_descriptor:
            if hasattr(rel_obj_descriptor, 'get_related_api_objects'):
                something = rel_obj_descriptor
                if through_attr != to_attr:
                    # Special case cached_property instances because hasattr
                    # triggers attribute computation and assignment.
                    if isinstance(getattr(instance.__class__, to_attr, None), cached_property):
                        is_fetched = to_attr in instance.__dict__
                    else:
                        is_fetched = rel_obj_descriptor.is_cached(instance)
                else:
                    raise ValueError('through_attr must not equal to to_attr')
    return something, rel_obj_descriptor, attr_found, is_fetched


def select_one_level(instances, something_for_select, lookup, level):
    rel_qs, rel_obj_attr, instance_attr = (
        something_for_select.get_related_api_objects(instances))

    all_related_objects = list(rel_qs)

    rel_obj_cache = {}
    for rel_obj in all_related_objects:
        rel_attr_val = rel_obj_attr(rel_obj)
        rel_obj_cache.setdefault(rel_attr_val, []).append(rel_obj)

    to_attr, as_attr = lookup.get_current_to_attr(level)
    # Make sure `to_attr` does not conflict with a field.
    if as_attr and instances:
        # We assume that objects retrieved are homogeneous (which is the premise
        # of select_api_related), so what applies to first object applies to all.
        model = instances[0].__class__
        try:
            model._meta.get_field(to_attr)
        except exceptions.FieldDoesNotExist:
            pass
        else:
            msg = 'select_api_related to_attr={} conflicts with a field on the {} model.'
            raise ValueError(msg.format(to_attr, model.__name__))

    for obj in instances:
        instance_attr_val = instance_attr(obj)
        if isinstance(instance_attr_val, list):
            val = []
            for instance_attr_val_one in instance_attr_val:
                val_one = rel_obj_cache.get(instance_attr_val_one, [])
                if val_one:
                    val.append(val_one[0])
        else:
            vals = rel_obj_cache.get(instance_attr_val, [])

            val = vals[0] if vals else None
        something_for_select.set_cache(obj, val)
        setattr(obj, to_attr, val)
    return all_related_objects
