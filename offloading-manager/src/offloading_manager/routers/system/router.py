from typing import Protocol
from fastapi import APIRouter
from offloading_manager.routers.schemas import SystemStatusResponse
from offloading_manager.type import ModuleType, Stats


class SystemModel(Protocol):
    def get_module_stats(self) -> dict[ModuleType, Stats]: ...

    def get_robot_offloading_capable(self) -> int: ...

    def get_robot_in_module_offloading(self) -> dict[ModuleType, int]: ...


class SystemRouter:
    def __init__(self, system_model: SystemModel):
        self._system_router = APIRouter()

        @self._system_router.get(
            "/stress/system",
            response_model=SystemStatusResponse,
            summary="Get system stress information",
            description="Returns the current CPU and memory usage for each module, the number of offloading-capable robots, and the number of robots currently in offloading.",
        )
        async def get_system_stress() -> SystemStatusResponse:
            module_stats = system_model.get_module_stats()
            return SystemStatusResponse(
                cpu_usage={
                    module_type: stats.cpu_usage
                    for module_type, stats in module_stats.items()
                },
                memory_usage={
                    module_type: stats.memory_usage
                    for module_type, stats in module_stats.items()
                },
                offloading_capable_robot=system_model.get_robot_offloading_capable(),
                robot_in_offloading=system_model.get_robot_in_module_offloading(),
            )

    @property
    def router(self) -> APIRouter:
        return self._system_router
