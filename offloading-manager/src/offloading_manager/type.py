from pydantic import BaseModel
from enum import Enum


type RobotID = int


class OffloadingType(BaseModel):
    aggregate: bool = False
    aruco: bool = False
    neighbor: bool = False


class ModuleType(Enum):
    AGGREGATE = "aggregate"
    ARUCO = "aruco"
    NEIGHBOR = "neighbor"


class Stats(BaseModel):
    cpu_usage: float
    memory_usage: float
