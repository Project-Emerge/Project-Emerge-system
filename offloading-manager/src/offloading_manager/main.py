from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from offloading_manager.core.decision_module import OnlyDeleteDecisionModule
from offloading_manager.core.model import Model
from offloading_manager.routers.module.router import ModuleRouter
from offloading_manager.routers.robot.router import RobotRouter
from offloading_manager.routers.state.router import StateRouter
from offloading_manager.routers.system.router import SystemRouter
from offloading_manager.module_monitor.module_monitor import ModuleMonitor
import asyncio


def create_model():
    return Model(OnlyDeleteDecisionModule)


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
