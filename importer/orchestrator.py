from django.utils import timezone
import logging
from django.contrib.auth import get_user_model
from geonode.resource.models import ExecutionRequest
from importer.api.exception import ImportException
from importer.handlers.vector import GPKGFileHandler
from importer.celery_app import app
from geonode.upload.models import Upload
from geonode.base.enumerations import STATE_RUNNING

logger = logging.getLogger(__name__)


SUPPORTED_TYPES = {
    "gpkg": GPKGFileHandler()
    # "vector": VectorFileHandler
}


class ImportOrchestrator:
    ''''
    Main import object. Is responsible to handle all the execution steps
    Using the ExecutionRequest object, will extrapolate the information and
    it call the next step of the import pipeline
    Params: 
    
    enable_legacy_upload_status default=True: if true, will save the upload progress
        also in the legacy upload system
    '''
    def __init__(self, enable_legacy_upload_status=True) -> None:
        self.enable_legacy_upload_status = enable_legacy_upload_status

    @property
    def supported_type(self):
        """
        Returns the supported types for the import
        """
        return SUPPORTED_TYPES.keys()

    def get_file_handler(self, file_type):
        """
        Returns the supported types for the import
        """
        _type = SUPPORTED_TYPES.get(file_type)
        if not _type:
            raise ImportException(
                detail=f"The requested filetype is not supported: {file_type}"
            )
        return _type

    def get_execution_object(self, exec_id):
        '''
        Returns the ExecutionRequest object with the detail about the 
        current execution
        '''
        req = ExecutionRequest.objects.filter(exec_id=exec_id).first()
        if req is None:
            raise ImportException("The selected UUID does not exists")
        return req

    def perform_next_import_step(self, resource_type: str, execution_id: str) -> None:
        '''
        It takes the executionRequest detail to extract which was the last step
        and take from the task_lists provided by the ResourceType handler
        which will be the following step. if empty a None is returned, otherwise
        in async the next step is called
        '''
        # Getting the execution object
        _exec = self.get_execution_object(str(execution_id))
        # retrieve the task list for the resource_type
        tasks = self.get_file_handler(resource_type).TASKS_LIST
        # getting the index
        try:
            _index = tasks.index(_exec.step) + 1
            # finding in the task_list the last step done
            remaining_tasks = tasks[_index:] if not _index >= len(tasks) else []
            if not remaining_tasks:
                return
            # getting the next step to perform
            next_step = next(iter(remaining_tasks))
            # calling the next step for the resource
            app.tasks.get(next_step).apply_async(
                (
                    resource_type,
                    str(execution_id),
                )
            )

        except StopIteration:
            # means that the expected list of steps is completed
            logger.info("The whole list of tasks has been processed")
            return
        except Exception as e:
            self.set_as_failed(execution_id)
            raise ImportException(detail=e.args[0])

    def set_as_failed(self, execution_id):
        '''
        Utility method to set the ExecutionRequest object to fail
        '''
        self.update_execution_request_status(
                execution_id=str(execution_id),
                status=ExecutionRequest.STATUS_FAILED,
                finished=timezone.now(),
                last_updated=timezone.utcnow(),
            )

    def create_execution_request(
        self,
        user: get_user_model,
        func_name: str,
        step: str,
        input_params: dict,
        resource=None,
    ) -> str:
        """
        Create an execution request for the user. Return the UUID of the request
        """
        execution = ExecutionRequest.objects.create(
            user=user,
            geonode_resource=resource,
            func_name=func_name,
            step=step,
            input_params=input_params,
        )
        if self.enable_legacy_upload_status:
            Upload.objects.create(
                state=STATE_RUNNING,
                metadata={
                    **{
                        "func_name": func_name,
                        "step": step,
                        "exec_id": str(execution.exec_id),
                    },
                    **input_params,
                },
            )
        return execution.exec_id

    def update_execution_request_status(self, execution_id, status, **kwargs):
        ExecutionRequest.objects.filter(exec_id=execution_id).update(
            status=status, **kwargs
        )
        if self.enable_legacy_upload_status:
            Upload.objects.filter(metadata__contains=execution_id).update(
                state=status, metadata={**kwargs, **{"exec_id": execution_id}}
            )
