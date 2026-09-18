"""The two setup documents: the shared roster, and this PC's share of it.

The arrangement under test throughout is the one that broke: four cameras, one on
the fusion server PC, two on a second PC, one on a third.
"""

from pathlib import Path

import pytest

from vision_system.core.config import AppConfig, CameraConfig, load_config, save_json
from vision_system.core.setup import (
    DeploymentSetup,
    apply_setup,
    launch_commands,
    load_setup,
    load_template,
    numbered_roster,
    parse_roster,
    resolve_roster,
    save_setup,
    select_cameras,
    setup_path,
    source_index,
    stable_camera_source,
)

FOUR = AppConfig()
ROSTER_IDS = ("cam_0", "cam_1", "cam_2", "cam_3")


def _roster(config: AppConfig) -> list[str]:
    return [camera.id for camera in config.cameras]


# ------------------------------------------------------------------- roster ---
def test_naming_the_cameras_makes_the_roster_exactly_those():
    """The whole point: a deployment can be cam_2 and cam_3, not cam_0 and cam_1."""
    named = resolve_roster(FOUR, ["cam_2", "cam_3"])
    assert _roster(named) == ["cam_2", "cam_3"]
    # Their sources came along untouched, so their calibrations still apply.
    assert [camera.source for camera in named.cameras] == [2, 4]


def test_a_named_roster_keeps_the_order_it_was_given():
    assert _roster(resolve_roster(FOUR, ["cam_3", "cam_1"])) == ["cam_3", "cam_1"]


def test_naming_a_camera_that_does_not_exist_yet_creates_it():
    one = AppConfig(cameras=[CameraConfig(id="cam_2", source=7)])
    grown = resolve_roster(one, ["cam_2", "cam_3"])
    assert _roster(grown) == ["cam_2", "cam_3"]
    assert [camera.source for camera in grown.cameras] == [7, 0]


def test_a_roster_cannot_repeat_a_camera_or_be_empty():
    with pytest.raises(ValueError, match="only appear once"):
        resolve_roster(FOUR, ["cam_0", "cam_0"])
    with pytest.raises(ValueError, match="at least one camera"):
        resolve_roster(FOUR, [])
    with pytest.raises(ValueError, match="at most 4"):
        resolve_roster(FOUR, ["cam_0", "cam_1", "cam_2", "cam_3", "cam_4"])


def test_a_count_fills_the_roster_up_from_cam_0():
    two = AppConfig(
        cameras=[CameraConfig(id="cam_0", source=7), CameraConfig(id="cam_1", source=2)]
    )
    grown = resolve_roster(two, numbered_roster(two, 4))
    assert _roster(grown) == ["cam_0", "cam_1", "cam_2", "cam_3"]
    assert [camera.source for camera in grown.cameras][:2] == [7, 2]


def test_a_count_keeps_the_cameras_already_configured_whatever_they_are_called():
    odd = AppConfig(cameras=[CameraConfig(id="cam_2", source=3)])
    grown = resolve_roster(odd, numbered_roster(odd, 3))
    assert _roster(grown) == ["cam_0", "cam_1", "cam_2"]
    assert sorted(camera.source for camera in grown.cameras) == [0, 1, 3]


def test_a_smaller_count_drops_the_tail_of_the_roster():
    assert numbered_roster(FOUR, 2) == ["cam_0", "cam_1"]


@pytest.mark.parametrize("count", [0, 5])
def test_the_roster_size_is_bounded(count):
    with pytest.raises(ValueError, match="1 to 4"):
        numbered_roster(FOUR, count)


# ------------------------------------------------------------------- parsing ---
def test_ids_and_a_bare_count_are_told_apart():
    assert parse_roster(["cam_2", "cam_3"], FOUR) == ["cam_2", "cam_3"]
    assert parse_roster(["2"], FOUR) == ["cam_0", "cam_1"]
    assert parse_roster([], FOUR) == ["cam_0", "cam_1", "cam_2", "cam_3"]


def test_mixing_a_count_with_ids_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="not both"):
        parse_roster(["2", "cam_3"], FOUR)


def test_a_roster_that_changes_nothing_does_not_bump_the_revision():
    """Nodes reload on a revision change; saving an unedited form must not stir them."""
    unchanged = apply_setup(AppConfig(revision=4), DeploymentSetup(), list(ROSTER_IDS))
    assert unchanged.revision == 4
    resized = apply_setup(AppConfig(revision=4), DeploymentSetup(), ["cam_0", "cam_1"])
    assert resized.revision == 5


# -------------------------------------------------------------- local share ---
def test_single_pc_owns_the_whole_roster_whatever_is_stored():
    setup = DeploymentSetup(mode="single-pc", local_camera_ids=["cam_1"])
    assert setup.local_ids(FOUR) == ["cam_0", "cam_1", "cam_2", "cam_3"]
    assert setup.remote_ids(FOUR) == []


def test_a_client_pc_owns_two_cameras_and_leaves_the_other_two_to_their_owners():
    setup = DeploymentSetup(mode="distributed", local_camera_ids=["cam_1", "cam_2"])
    assert setup.local_ids(FOUR) == ["cam_1", "cam_2"]
    assert setup.remote_ids(FOUR) == ["cam_0", "cam_3"]


def test_a_pure_server_pc_owns_no_camera_at_all():
    setup = DeploymentSetup(mode="distributed")
    assert setup.local_ids(FOUR) == []
    assert setup.remote_ids(FOUR) == ["cam_0", "cam_1", "cam_2", "cam_3"]


def test_selecting_cameras_returns_them_in_roster_order():
    assert [c.id for c in select_cameras(FOUR, ["cam_3", "cam_0"])] == ["cam_0", "cam_3"]
    assert select_cameras(FOUR, None) == list(FOUR.cameras)


def test_a_local_camera_outside_the_roster_is_refused():
    with pytest.raises(ValueError, match="cam_9"):
        select_cameras(FOUR, ["cam_9"])
    with pytest.raises(ValueError, match="only be selected once"):
        select_cameras(FOUR, ["cam_0", "cam_0"])


def test_a_broker_url_is_not_a_hostname():
    with pytest.raises(ValueError, match="URL scheme"):
        DeploymentSetup(mqtt_host="tcp://192.168.1.10")
    assert DeploymentSetup(mqtt_host="  192.168.1.10 ").mqtt_host == "192.168.1.10"


# ------------------------------------------------------------------ on disk ---
def test_saving_writes_both_documents_next_to_each_other(tmp_path):
    config_path = tmp_path / "config.local.json"
    save_json(config_path, FOUR)
    setup = DeploymentSetup(
        mode="distributed", local_camera_ids=["cam_1", "cam_2"], mqtt_host="192.168.1.10"
    )

    config = save_setup(config_path, setup, ["4"])

    assert _roster(config) == ["cam_0", "cam_1", "cam_2", "cam_3"]
    assert _roster(load_config(config_path)) == _roster(config)
    assert setup_path(config_path) == tmp_path / "config.local.setup.json"
    assert load_setup(config_path) == setup


def test_a_missing_setup_file_means_a_single_pc_deployment(tmp_path):
    assert load_setup(tmp_path / "config.local.json") == DeploymentSetup()


def test_saving_from_nothing_starts_at_the_requested_size(tmp_path):
    """A fresh file must not inherit the built-in four-camera default."""
    config_path = tmp_path / "config.local.json"
    assert _roster(save_setup(config_path, DeploymentSetup(), ["2"])) == ["cam_0", "cam_1"]


def test_neither_document_is_written_when_the_pair_does_not_hold_together(tmp_path):
    """Shrinking away a camera this PC owns is caught before anything is on disk."""
    config_path = tmp_path / "config.local.json"
    save_json(config_path, FOUR)
    setup = DeploymentSetup(mode="distributed", local_camera_ids=["cam_3"])

    with pytest.raises(ValueError, match="cam_3"):
        save_setup(config_path, setup, ["2"])

    assert _roster(load_config(config_path)) == ["cam_0", "cam_1", "cam_2", "cam_3"]
    assert not setup_path(config_path).exists()


# ---------------------------------------------------------------- commands ---
def test_the_single_pc_commands_are_the_one_stack(tmp_path):
    lines = launch_commands(tmp_path / "c.json", FOUR, DeploymentSetup())
    assert "make all" in lines
    assert not any("make client" in line for line in lines)


def test_the_distributed_commands_cover_one_node_per_local_camera(tmp_path):
    setup = DeploymentSetup(
        mode="distributed", local_camera_ids=["cam_1", "cam_2"], mqtt_host="192.168.1.10"
    )
    lines = launch_commands(tmp_path / "c.json", FOUR, setup)
    clients = [line for line in lines if line.startswith("make client")]
    assert len(clients) == 2
    assert "CAMERA=cam_1" in clients[0] and "MQTT_HOST=192.168.1.10" in clients[0]
    assert "make server" in lines
    # The cameras this PC does not own still have to be started somewhere.
    assert any("cam_0, cam_3" in line for line in lines)


def test_a_broker_on_this_pc_is_reached_through_the_container_gateway(tmp_path):
    setup = DeploymentSetup(mode="distributed", local_camera_ids=["cam_0"])
    clients = [
        line
        for line in launch_commands(tmp_path / "c.json", FOUR, setup)
        if line.startswith("make client")
    ]
    assert "MQTT_HOST=host.docker.internal" in clients[0]


# ------------------------------------------------------------ stable source ---
def test_a_source_with_no_by_id_alias_is_left_exactly_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr("vision_system.core.setup.STABLE_DEVICE_DIR", tmp_path / "absent")
    assert stable_camera_source(3) == 3
    assert stable_camera_source("/dev/video3") == "/dev/video3"


def test_a_video_node_is_replaced_by_the_alias_that_points_at_it(tmp_path, monkeypatch):
    device = tmp_path / "video3"
    device.write_text("")
    aliases = tmp_path / "by-id"
    aliases.mkdir()
    (aliases / "usb-Acme_Webcam-video-index0").symlink_to(device)
    monkeypatch.setattr("vision_system.core.setup.STABLE_DEVICE_DIR", aliases)

    resolved = stable_camera_source(str(device))

    assert resolved == str(aliases / "usb-Acme_Webcam-video-index0")
    assert Path(resolved).resolve() == device


def test_a_by_id_alias_resolves_back_to_the_index_the_preview_grid_uses(tmp_path, monkeypatch):
    device = tmp_path / "video1"
    device.write_text("")
    alias = tmp_path / "usb-Acme_Webcam-video-index0"
    alias.symlink_to(device)
    assert source_index(str(alias)) == 1
    assert source_index(3) == 3
    assert source_index(str(tmp_path / "absent")) is None


# --------------------------------------------------------------- the example ---
EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def test_a_roster_built_from_nothing_starts_on_the_documented_example(tmp_path):
    """Setting the camera count is starting from scratch: cut it from the example."""
    template = load_template(EXAMPLE)
    config = save_setup(tmp_path / "config.local.json", DeploymentSetup(), ["2"], template=template)

    assert [(c.id, c.source) for c in config.cameras] == [("cam_0", 5), ("cam_1", 1)]
    assert config.site == template.site
    assert config.aruco.mobile_markers == template.aruco.mobile_markers


def test_cameras_added_to_an_existing_roster_come_from_the_example_too(tmp_path):
    config_path = tmp_path / "config.local.json"
    save_json(config_path, AppConfig(cameras=[CameraConfig(id="cam_2", source="/dev/video9")]))

    config = save_setup(
        config_path, DeploymentSetup(), ["4"], template=load_template(EXAMPLE)
    )

    # cam_2 was already configured, so it keeps its own source; the rest arrive
    # on the source numbers the example documents.
    assert [(c.id, c.source) for c in config.cameras] == [
        ("cam_0", 5),
        ("cam_1", 1),
        ("cam_2", "/dev/video9"),
        ("cam_3", 4),
    ]


def test_settings_already_chosen_are_never_overwritten_by_the_example(tmp_path):
    config_path = tmp_path / "config.local.json"
    mine = AppConfig(site="factory", cameras=[CameraConfig(id="cam_0", source=7, fps=15.0)])
    save_json(config_path, mine)

    config = save_setup(
        config_path, DeploymentSetup(), ["2"], template=load_template(EXAMPLE)
    )

    assert config.site == "factory"
    assert (config.cameras[0].source, config.cameras[0].fps) == (7, 15.0)


def test_an_example_camera_whose_source_is_taken_still_gets_a_free_one(tmp_path):
    """Two cameras on one device would look deliberate and behave badly."""
    config_path = tmp_path / "config.local.json"
    save_json(config_path, AppConfig(cameras=[CameraConfig(id="cam_2", source=1)]))

    config = save_setup(config_path, DeploymentSetup(), ["3"], template=load_template(EXAMPLE))

    sources = [c.source for c in config.cameras]
    assert len(set(sources)) == len(sources)
    assert ("cam_1", 1) not in [(c.id, c.source) for c in config.cameras]


def test_a_missing_example_is_not_an_error_just_a_plainer_roster(tmp_path):
    assert load_template(tmp_path / "absent.json") is None
    assert load_template(None) is None
    config = save_setup(tmp_path / "config.local.json", DeploymentSetup(), ["1"], template=None)
    assert [c.id for c in config.cameras] == ["cam_0"]


def test_a_relative_example_is_also_looked_for_next_to_the_configuration(tmp_path):
    (tmp_path / "config.example.json").write_text(EXAMPLE.read_text())
    found = load_template(Path("config.example.json"), tmp_path / "config.local.json")
    assert found is not None and found.site == "lab"
