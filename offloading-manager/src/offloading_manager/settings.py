from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):

    """
    Settings for the Offloading Manager.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    only_delete_decision_max_cpu: int = Field(
        default=80,
        description="The maximum CPU usage percentage for a robot to be considered for offloading deletion",
    )

    only_delete_decision_max_memory: int = Field(
        default=80,
        description="The maximum memory usage percentage for a robot to be considered for offloading deletion",
    )

    containers_network: str = Field(
        default="project-emerge-network",
        description="The network mode to use for the Docker containers",
    )

    containers_polling_interval: int = Field(
        default=5,
        description="The interval (in seconds) at which to poll the Docker containers for stats",
    )

    robot_response_timeout: int = Field(
        default=10,
        description="The maximum time (in seconds) to wait for a response from a robot before considering it unresponsive",
    )



settings = Settings()