from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from offloading_manager.core.decision_module import (
    OnlyDeleteDecisionModule,
    testOthersModuleConnectionDecisionModule,
)
from offloading_manager.core.model import Model
from offloading_manager.routers.module.router import ModuleRouter
from offloading_manager.routers.robot.router import RobotRouter
from offloading_manager.routers.state.router import StateRouter
from offloading_manager.routers.system.router import SystemRouter
from offloading_manager.module_monitor.module_monitor import ModuleMonitor
import asyncio
import os

# Decision module selectable via env. direct forwards every REST offloading
# request straight to the connected modules without requiring the robot itself
# to hold a WebSocket connection to the manager (useful when robots only speak
# MQTT, e.g. the robot-emulation + control-panel demo).
_DECISION_MODULES = {
    "only_delete": OnlyDeleteDecisionModule,
    "direct": testOthersModuleConnectionDecisionModule,
}


def create_model():
    name = os.getenv("DECISION_MODULE", "only_delete").strip().lower()
    decision_module = _DECISION_MODULES.get(name, OnlyDeleteDecisionModule)
    return Model(decision_module)


model = create_model()


@asynccontextmanager
async def lifespan(app: FastAPI):
    docker_monitor = ModuleMonitor(model)
    task = asyncio.create_task(docker_monitor.start())
    yield
    docker_monitor.stop()
    task.cancel()


app = FastAPI(lifespan=lifespan)

# just for testing, to be removed later
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(ModuleRouter(model).router)
app.include_router(RobotRouter(model).router)
app.include_router(StateRouter(model).router)
app.include_router(SystemRouter(model).router)