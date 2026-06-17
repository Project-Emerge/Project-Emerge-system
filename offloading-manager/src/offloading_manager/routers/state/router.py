from typing import Protocol
from offloading_manager.type import OffloadingType, RobotID
from fastapi import APIRouter, HTTPException
from offloading_manager.routers.schemas import (
    RobotStatusResponse,
    StatusResponse,
    RequestResponse,
    OffloadingRequest,
)


class StateModel(Protocol):
    def get_all_robots_state(self) -> dict[RobotID, OffloadingType]: ...

    def get_robot_state(self, robot_id: RobotID) -> OffloadingType | None: ...

    async def offloading_request_consideration(
        self, robot_id: RobotID, offloading_type: OffloadingType
    ) -> RequestResponse: ...


class StateRouter:
    def __init__(self, state_model: StateModel):
        self._state_router = APIRouter()

        @self._state_router.get(
            "/",
            response_model=StatusResponse,
            summary="Get offloading status of all robots",
            description="Returns a map of every known robot ID to its current offloading state.",
        )
        async def get_all_offloading_status() -> StatusResponse:
            return StatusResponse(robots=state_model.get_all_robots_state())

        @self._state_router.get(
            "/{robot_id}",
            response_model=RobotStatusResponse,
            summary="Get offloading status of a single robot",
            description="Returns the current offloading state, type, and registered endpoint for the given robot.",
        )
        async def get_robot_status(robot_id: RobotID) -> RobotStatusResponse:
            robot_state = state_model.get_robot_state(robot_id)
            if not robot_state:
                raise HTTPException(status_code=404, detail="Robot not found")
            return RobotStatusResponse(id=robot_id, state=robot_state)

        @self._state_router.put(
            "/{robot_id}",
            response_model=RequestResponse,
            summary="Request offloading for a robot",
            description="Requests offloading status change for a robot with the given ID.",
        )
        async def put_robot_status(
            robot_id: RobotID, body: OffloadingRequest
        ) -> RequestResponse:
            return await state_model.offloading_request_consideration(
                robot_id, body.type
            )

    @property
    def router(self) -> APIRouter:
        return self._state_router
