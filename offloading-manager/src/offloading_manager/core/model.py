from offloading_manager.core.decision_module import DecisionModule
from offloading_manager.core.module_registry import ModuleRegistry
from offloading_manager.core.robot_registry import RobotRegistry
from offloading_manager.routers.schemas import RequestResponse
from offloading_manager.routers.module.module import Module
from offloading_manager.routers.robot.robot import Robot
from offloading_manager.type import ModuleType, OffloadingType, Stats, RobotID


class Model:
    def __init__(self, typeOfDecisionModule: type[DecisionModule]):
        self._robotRegistry: RobotRegistry = RobotRegistry()
        self._moduleRegistry: ModuleRegistry = ModuleRegistry()
        self._decision_module: DecisionModule = typeOfDecisionModule(
            self._robotRegistry, self._moduleRegistry
        )

    def add_robot(self, robot: Robot, robot_id: RobotID) -> None:
        """Add a robot to the system with an initial offloading state of not offloading in any module
        Args:
            robot_id (int): the id of the robot to add
        """

        self._robotRegistry.add_robot_connection(robot, robot_id)

    def remove_robot(self, robot_id: RobotID) -> None:
        """Remove a robot from the system
        Args:
            robot_id (int): the id of the robot to remove
        """

        self._robotRegistry.remove_robot_connection(robot_id)

    def add_module(self, module: Module, module_type: ModuleType) -> None:
        """Add a module to the system
        Args:
            module_type (ModuleType): the type of the module to add
            module: the module connection to add
        """

        self._moduleRegistry.add_module_connection(module_type, module)

    def remove_module(self, module_type: ModuleType) -> None:
        """Remove a module from the system
        Args:
            module_type (ModuleType): the type of the module to remove
        """

        self._moduleRegistry.remove_module_connection(module_type)

    def get_module_stats(self) -> dict[ModuleType, Stats]:
        """Get the saved stats of all modules
        Returns:
            dict[ModuleType, Stats]: a dictionary mapping module types to their saved stats
        """

        return self._moduleRegistry.get_modules_stats()

    def get_robot_offloading_capable(self) -> int:
        """Get the number of robots that are offloading capable
        Returns:
            int: the number of robots that are offloading capable
        """

        return self._robotRegistry.get_drone_offloading_capable()

    def get_robot_in_module_offloading(self) -> dict[ModuleType, int]:
        """Get the number of robots that are offloading in each module
        Returns:
            dict[ModuleType, int]: a dictionary mapping module types to the number of robots that are currently offloading in that module
        """

        return self._robotRegistry.get_robot_in_module_offloading()

    def get_all_robots_state(self) -> dict[RobotID, OffloadingType]:
        """Get the offloading state of all robots
        Returns:
            dict[RobotID, OffloadingType]: a dictionary mapping robot IDs to their offloading states
        """

        return self._robotRegistry.get_all_robots_state()

    def get_robot_state(self, robot_id: RobotID) -> OffloadingType | None:
        """Get the offloading state of a single robot
        Args:
            robot_id (int): the id of the robot to get the state of
        Returns:
            Optional[OffloadingType]: the offloading state of the robot, or None if the robot is not found
        """

        return self._robotRegistry.get_robot_state(robot_id)

    async def offloading_request_consideration(
        self, robot_id: RobotID, offloading_type: OffloadingType
    ) -> RequestResponse:
        """Consider an offloading request for a robot and update the state accordingly
        Args:
            robot_id (int): the id of the robot making the request
            offloading_type (OffloadingType): the type of offloading that the robot is requesting
        Returns:
            RequestResponse: the response to the offloading request
        """

        return await self._decision_module.offloading_request_consideration(
            robot_id, offloading_type
        )

    async def self_offloading_request_consideration(
        self, robot_id: RobotID, offloading_type: OffloadingType, message_id: int
    ) -> None:
        """Consider an offloading request initiated by the robot itself, update the state accordingly, and respond to the robot
        Args:
            robot_id (int): the id of the robot making the request
            offloading_type (OffloadingType): the type of offloading that the robot is requesting
            message_id (int): the id of the incoming message to respond to
        """

        return await self._decision_module.offloading_self_request_consideration(
            robot_id, offloading_type, message_id
        )

    async def stats_valutation(self) -> None:
        """Perform a stats valutation using the decision module and update the state accordingly"""

        await self._decision_module.stats_valutation()

    def update_module_stats(self, module_type: ModuleType, stats: Stats) -> None:
        """Update the saved stats of a module
        Args:
            module_type (ModuleType): the type of the module to update the stats of
            stats (Stats): the new stats to save for the module
        """

        self._moduleRegistry.update_module_stats(module_type, stats)
