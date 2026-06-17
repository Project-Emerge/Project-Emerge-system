from typing import Protocol

from offloading_manager.type import OffloadingType, RobotID
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from offloading_manager.routers.robot.robot import Robot


class RobotRouterModel(Protocol):
    def add_robot(self, robot: Robot, robot_id: RobotID) -> None: ...

    def remove_robot(self, robot_id: RobotID) -> None: ...

    async def self_offloading_request_consideration(
        self, robot_id: RobotID, offloading_type: OffloadingType, message_id: int
    ) -> None: ...


class RobotRouter:
    def __init__(self, robot_model: RobotRouterModel):
        self._robot_router = APIRouter()

        @self._robot_router.websocket("/ws/robots/{robot_id}")
        async def robot_websocket(websocket: WebSocket, robot_id: RobotID):
            robot = Robot(websocket, robot_id, robot_model)
            await robot.connect()
            robot_model.add_robot(robot, robot_id)
            try:
                await robot.listen()
            except WebSocketDisconnect:
                pass
            finally:
                await robot.disconnect()
                robot_model.remove_robot(robot_id)

    @property
    def router(self) -> APIRouter:
        return self._robot_router
