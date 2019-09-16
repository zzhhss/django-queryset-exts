from django.dispatch import Signal

pre_update = Signal(providing_args=["queryset", "kwargs"])
post_update = Signal(providing_args=["queryset", "kwargs", "result"])
