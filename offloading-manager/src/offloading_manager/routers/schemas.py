from pydantic import BaseModel
from offloading_manager.type import ModuleType
from offloading_manager.type import OffloadingType


class SystemStatusResponse(BaseModel):
    cpu_usage: dict[ModuleType, float]
    memory_usage: dict[ModuleType, float]
    offloading_capable_robot: int
    robot_in_offloading: dict[ModuleType, int]


class StatusResponse(BaseModel):
    robots: dict[int, OffloadingType]


class RobotStatusResponse(BaseModel):
    id: int
    state: OffloadingType


class RequestResponse(BaseModel):
    success: bool


class OffloadingRequest(BaseModel):
    id: int
    type: OffloadingType


class ChangeState(BaseModel):
    id: int
    calc: bool
