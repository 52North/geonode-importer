from dynamic_models.models import FieldSchema, ModelSchema
from geonode.geoserver.signals import geoserver_delete
import logging
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver
from geonode.layers.models import Dataset

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=Dataset)
def delete_dynamic_model(instance, sender, **kwargs):
    '''
    Delete the dynamic relation and the publishde geoserver layer
    '''
    try:
        name = instance.alternate.split(":")[1]
        ModelSchema.objects.filter(name=name).delete()
        FieldSchema.objects.filter(name=name).delete()
        geoserver_delete(instance.alternate)
        # Removing Field Schema
    except Exception as e:
        logger.error(f"Error during deletion of Dynamic Model schema: {e.args[0]}")
