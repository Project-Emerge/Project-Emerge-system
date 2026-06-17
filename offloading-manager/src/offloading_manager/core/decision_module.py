from __future__ import annotations
from abc import ABC, abstractmethod
from offloading_manager.settings import settings
from offloading_manager.core.module_registry import ModuleRegistry
from offloading_manager.core.robot_registry import RobotRegistry
from offloading_manager.type import OffloadingType, RobotID
from offloading_manager.routers.schemas import (
    RequestResponse,
    ChangeState,
    OffloadingRequest,
)
from offloading_manager.type import ModuleType
import logging

logger = logging.getLogger("uvicorn.error")


class DecisionModule(ABC):
    def __init__(self, robot_registry: RobotRegistry, module_registry: ModuleRegistry):
        self.robot_registry = robot_registry
        self.module_registry = module_registry

    @abstractmethod
    async def offloading_request_consideration(
        self, robot_id: RobotID, request_type: OffloadingType
    ) -> RequestResponse:
        pass

    @abstractmethod
    async def offloading_self_request_consideration(
        self, robot_id: RobotID, request_type: OffloadingType, message_id: int
    ) -> None:
        pass

    @abstractmethod
    async def stats_valutation(self):
        pass

    @abstractmethod
    async def _remove_robot_from_offloading(
        self, robot_id: RobotID, module_type: ModuleType
    ) -> bool | None:
        pass

    @abstractmethod
    async def _add_robot_to_offloading(
        self, robot_id: RobotID, module_type: ModuleType
    ) -> bool | None:
        pass


class StandardDecisionModule(DecisionModule, ABC):
    async def offloading_request_consideration(
        self, robot_id: RobotID, request_type: OffloadingType
    ) -> RequestResponse:
        """Considers an offloading request from a robot, updates the offloading state, and notifies the relevant modules.
        Args:
            robot_id (RobotID): the id of the robot making the request
            request_type (OffloadingType): the requested offloading state for the robot
            state (State): the current state of the system
            connection_registry (ConnectionRegistry): the registry managing connections
        Returns:
            RequestResponse: the result of the offloading request consideration, indicating success or failure
        """

        logger.info(f"Offloading request for robot {robot_id}: {request_type}")
        robot_connection = self.robot_registry.get_robot_connection(robot_id)
        if robot_connection is not None:
            result = await robot_connection.change_status_request(
                OffloadingRequest(id=robot_id, type=request_type)
            )
            if result is not None:
                logger.info(
                    f"Offloading request for robot {robot_id} {'accepted' if result else 'rejected'}"
                )
                if result:
                    for module in ModuleType:
                        module_connection = self.module_registry.get_module_connection(
                            module
                        )
                        if module_connection is not None:
                            if getattr(request_type, module.value):
                                await module_connection.change_status_request(
                                    ChangeState(id=robot_id, calc=True)
                                )
                            else:
                                await module_connection.change_status_request(
                                    ChangeState(id=robot_id, calc=False)
                                )
                    self.robot_registry.change_offloading_state(robot_id, request_type)
                return RequestResponse(success=result)
        return RequestResponse(success=False)

    async def offloading_self_request_consideration(
        self, robot_id: RobotID, request_type: OffloadingType, message_id: int
    ) -> None:
        """Considers an offloading request initiated by the robot itself, updates the offloading state, and notifies the relevant modules.
        Args:
            robot_id (RobotID): the id of the robot making the request
            request_type (OffloadingType): the requested offloading state for the robot
            state (State): the current state of the system
            message_id (int): the id of the incoming message to respond to
        """

        for module in ModuleType:
            module_connection = self.module_registry.get_module_connection(module)
            if module_connection is not None:
                if getattr(request_type, module.value):
                    await module_connection.change_status_request(
                        ChangeState(id=robot_id, calc=True)
                    )
                else:
                    await module_connection.change_status_request(
                        ChangeState(id=robot_id, calc=False)
                    )
        self.robot_registry.change_offloading_state(robot_id, request_type)
        robot_connection = self.robot_registry.get_robot_connection(robot_id)
        if robot_connection:
            await robot_connection.respond(True, message_id)

    @abstractmethod
    async def stats_valutation(self):
        pass

    async def _remove_robot_from_offloading(
        self, robot_id: RobotID, module_type: ModuleType
    ) -> bool | None:
        """Removes a robot from offloading in a specific module, updates the offloading state, and notifies the relevant modules.
        Args:
            robot_id (RobotID): the id of the robot to remove from offloading
            module_type (ModuleType): the module from which to remove the robot from offloading
            state (State): the current state of the system
        Returns:
            bool | None: the result of the operation, or None if the robot or module was not found 
        """

        offloading_module = self.robot_registry.get_robot_state(robot_id)
        if offloading_module is not None:
            offloading_request = offloading_module.model_copy(
                update={module_type.value: False}
            )
            result = await self.offloading_request_consideration(
                robot_id, offloading_request
            )
            logger.info(f"Removing robot {robot_id} from module {module_type.value}")
            return result.success

    async def _add_robot_to_offloading(
        self, robot_id: RobotID, module_type: ModuleType
    ) -> bool | None:
        """Adds a robot to offloading in a specific module, updates the offloading state, and notifies the relevant modules.
        Args:
            robot_id (RobotID): the id of the robot to add to offloading
            module_type (ModuleType): the module to which to add the robot to offloading
            state (State): the current state of the system
        Returns:
            bool | None: the result of the operation, or None if the robot or the module was not found
        """
        offloading_module = self.robot_registry.get_robot_state(robot_id)
        if offloading_module is not None:
            offloading_request = offloading_module.model_copy(
                update={module_type.value: True}
            )
            result = await self.offloading_request_consideration(
                robot_id, offloading_request
            )
            logger.info(f"Adding robot {robot_id} to module {module_type.value}")
            return result.success


class OnlyDeleteDecisionModule(StandardDecisionModule):
    async def stats_valutation(self):
        """Evaluates the current system stats and, if any module is under high stress, tries to remove robots from offloading in that module until the stress is reduced.
        Args:
            state (State): the current state of the system
        """

        all_stats = self.module_registry.get_modules_stats()
        for module, stats in all_stats.items():
            if (
                stats.cpu_usage > settings.only_delete_decision_max_cpu
                or stats.memory_usage > settings.only_delete_decision_max_memory
            ):
                logger.info(
                    f"Module {module} under stress (cpu={stats.cpu_usage:.1f}% mem={stats.memory_usage:.1f}%), attempting to remove a robot"
                )
                for robot_id in self.robot_registry.get_offloading_robots_in_module(
                    module
                ):
                    if await self._remove_robot_from_offloading(robot_id, module):
                        break

class testOthersModuleConnectionDecisionModule(DecisionModule):
    async def offloading_request_consideration(
        self, robot_id: RobotID, request_type: OffloadingType
    ) -> RequestResponse:
        for module in ModuleType:
            module_connection = self.module_registry.get_module_connection(
                module
            )
            if module_connection is not None:
                if getattr(request_type, module.value):
                    await module_connection.change_status_request(
                        ChangeState(id=robot_id, calc=True)
                    )
                else:
                    await module_connection.change_status_request(
                        ChangeState(id=robot_id, calc=False)
                    )
        self.robot_registry.change_offloading_state(robot_id, request_type)
        return RequestResponse(success=True)

    async def offloading_self_request_consideration(
        self, robot_id: RobotID, request_type: OffloadingType, message_id: int
    ) -> None:
        pass

    async def stats_valutation(self):
        pass

    async def _remove_robot_from_offloading(
        self, robot_id: RobotID, module_type: ModuleType
    ) -> bool | None:
        return True

    async def _add_robot_to_offloading(
        self, robot_id: RobotID, module_type: ModuleType
    ) -> bool | None:
        return True