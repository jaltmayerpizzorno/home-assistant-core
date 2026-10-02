"""Tests for the Poolside sensors: water temperature and site mode."""

import json
from typing import Any

from aiopoolside import PoolsideControl, PoolsideSite
from aiopoolside.const import ControlType, GroupKind
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.sensor import ATTR_OPTIONS, SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .conftest import (
    TEST_BODY_OF_WATER_UUID,
    TEST_SITE_UUID,
    FakePoolsideClient,
    make_control,
    make_group,
)

from tests.common import MockConfigEntry, async_fire_time_changed

ENTITY_ID = "sensor.pool_temperature"
MODE_ENTITY_ID = "sensor.test_residence_controller_mode"
WATER_STATE_ENTITY_ID = "sensor.pool_water_state"
READY_TIME_ENTITY_ID = "sensor.pool_estimated_ready_time"
DISABLED_REASON_ENTITY_ID = "sensor.pool_heater_disabled_reason"


@pytest.fixture
def controls(hass: HomeAssistant) -> list[PoolsideControl]:
    """Controls sharing the pool group (one winterized), plus one on a landscape group.

    Only the pool (a group with a BodyOfWaterUUID) should get a temperature
    sensor, and only one of it. Uses imperial units so the sensor's Fahrenheit
    native unit matches the test hass's unit system and needs no conversion.
    """
    hass.config.units = US_CUSTOMARY_SYSTEM
    landscape = make_group("group-yard", "Yard", kind=GroupKind.LANDSCAPE)
    return [
        make_control("heater-1", "Heater", ControlType.TEMPERATURE),
        make_control("light-1", "Pool Light", ControlType.LIGHT),
        make_control("light-2", "Yard Light", ControlType.LIGHT, group=landscape),
        make_control("cleaner-1", "Cleaner", ControlType.CLEANER, Winterized=True),
    ]


@pytest.mark.usefixtures("setup_integration")
async def test_expected_sensors_created(hass: HomeAssistant) -> None:
    """Temperature and water state per body of water (none for landscape groups), a disabled reason sensor per control, plus the site mode sensor.

    Chemistry and ready time sensors are absent until their fields are actually reported.
    """
    assert set(hass.states.async_entity_ids("sensor")) == {
        ENTITY_ID,
        WATER_STATE_ENTITY_ID,
        MODE_ENTITY_ID,
        DISABLED_REASON_ENTITY_ID,
        "sensor.pool_pool_light_disabled_reason",
        "sensor.yard_yard_light_disabled_reason",
        "sensor.pool_cleaner_disabled_reason",
    }


@pytest.mark.parametrize(
    ("raw_value", "expected_state"),
    [
        pytest.param(79, "79.0", id="numeric"),
        pytest.param("79", "79.0", id="stringly-typed"),
    ],
)
@pytest.mark.usefixtures("setup_integration")
async def test_state_reflects_body_temperature_push(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
    raw_value: Any,
    expected_state: str,
) -> None:
    """The sensor renders Temperature pushes keyed by the body of water's UUID."""
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN

    fake_client.set_status(TEST_BODY_OF_WATER_UUID, "Temperature", raw_value)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == expected_state


@pytest.mark.parametrize(
    ("field", "entity_id", "raw_value", "expected_state", "expected_unit"),
    [
        pytest.param("ORP", "sensor.pool_orp", "650", "650.0", "mV", id="orp"),
        pytest.param("PH", "sensor.pool_ph", "7.4", "7.4", None, id="ph"),
        pytest.param(
            "FreeChlorine",
            "sensor.pool_free_chlorine",
            "1.5",
            "1.5",
            "ppm",
            id="free-chlorine",
        ),
        pytest.param(
            "TotalChlorine",
            "sensor.pool_total_chlorine",
            "1.8",
            "1.8",
            "ppm",
            id="total-chlorine",
        ),
        pytest.param(
            "DissolvedOxygenConcentration",
            "sensor.pool_dissolved_oxygen_concentration",
            "8.2",
            "8.2",
            "mg/L",
            id="dissolved-oxygen-concentration",
        ),
        pytest.param(
            "DissolvedOxygenSaturation",
            "sensor.pool_dissolved_oxygen_saturation",
            "95",
            "95.0",
            "%",
            id="dissolved-oxygen-saturation",
        ),
        pytest.param(
            "SaltLevel", "sensor.pool_salt_level", "3200", "3200.0", "ppm", id="salt"
        ),
    ],
)
@pytest.mark.usefixtures("setup_integration")
async def test_chemistry_sensor_added_when_reported(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
    field: str,
    entity_id: str,
    raw_value: str,
    expected_state: str,
    expected_unit: str | None,
) -> None:
    """Each chemistry sensor appears once its field is reported, with the right unit."""
    assert hass.states.get(entity_id) is None

    fake_client.set_status(TEST_BODY_OF_WATER_UUID, field, raw_value)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected_state
    assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == expected_unit


@pytest.mark.usefixtures("setup_integration")
async def test_water_state_sensor_reflects_pushes(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
) -> None:
    """The water state sensor renders CurrentState pushes, unknown values as unknown."""
    state = hass.states.get(WATER_STATE_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN

    fake_client.set_status(TEST_BODY_OF_WATER_UUID, "CurrentState", "HEATING")
    await hass.async_block_till_done()

    state = hass.states.get(WATER_STATE_ENTITY_ID)
    assert state is not None
    assert state.state == "heating"
    assert state.attributes[ATTR_OPTIONS] == [
        "off",
        "filtering",
        "heating",
        "cooling",
        "on",
        "critical_alert",
        "cooldown",
        "installer_mode",
    ]

    fake_client.set_status(TEST_BODY_OF_WATER_UUID, "CurrentState", "MAGMA")
    await hass.async_block_till_done()

    state = hass.states.get(WATER_STATE_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN


# The controller's placeholder for an unset timestamp, as sent on the wire.
UNSET_DATETIME = "0001-01-01T00:00:00-04:24"
# During a heat-up captured from a controller: sampled at 9:14:56 AM, ready
# predicted for 10:55:56 AM.
NOW = "2026-10-01T09:20:00-04:00"
PREDICTED = "2026-10-01T10:55:56.5610490-04:00"


def temperature_rise_information(**overrides: Any) -> dict[str, Any]:
    """Build a TemperatureRiseInformation document, shaped like a real one."""
    document: dict[str, Any] = {
        "HeaterUUIDs": [],
        "UUID": "rise-1",
        "HvacMode": "HEATING",
        "LastCalculatedDateTime": "2026-10-01T09:14:56.5610490-04:00",
        "LastCalculatedWaterTemperature": 82.655655,
        "StartTemperature": 82.655655,
        "TargetTemperature": 102.0,
        "SetPointChangedOrTStatWasTurnedOff": False,
        "StartDateTime": "2026-10-01T09:09:39.5644686-04:00",
        "TargetEndDateTime": PREDICTED,
        "OriginalTargetEndDateTime": PREDICTED,
        "ActualEndDateTime": UNSET_DATETIME,
        "ActualEndTemperature": 0.0,
        "EstimatedWattNeeded": 0,
        "EstimatedBTUNeeded": 0,
        "HeatingEventType": "NORMAL",
        "TemperatureState": "SAMPLED",
        "OriginalFutureHeatingEvent": None,
    }
    document.update(overrides)
    return document


def encoded(document: dict[str, Any]) -> str:
    """Encode a document the way status pushes carry it: JSON in a string."""
    return json.dumps(document, indent=2)


@pytest.mark.usefixtures("setup_integration")
async def test_ready_time_sensor_created_when_first_reported(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
) -> None:
    """The ready time sensor appears once the body of water reports a heat-up.

    Like the chemistry sensors: a body without a heater never reports one.
    """
    assert hass.states.get(READY_TIME_ENTITY_ID) is None

    fake_client.set_status(
        TEST_BODY_OF_WATER_UUID,
        "TemperatureRiseInformation",
        encoded(temperature_rise_information()),
    )
    await hass.async_block_till_done()

    state = hass.states.get(READY_TIME_ENTITY_ID)
    assert state is not None
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TIMESTAMP


@pytest.mark.parametrize(
    ("raw_value", "expected_state"),
    [
        pytest.param(
            encoded(temperature_rise_information()),
            "2026-10-01T14:55:56+00:00",
            id="predicted",
        ),
        pytest.param(
            temperature_rise_information(),
            "2026-10-01T14:55:56+00:00",
            id="native-document",
        ),
        pytest.param(
            encoded(temperature_rise_information(TemperatureState="SAMPLING")),
            STATE_UNKNOWN,
            id="preliminary-while-sampling",
        ),
        pytest.param(
            encoded(
                temperature_rise_information(HeatingEventType="MAINTAIN_TEMPERATURE")
            ),
            STATE_UNKNOWN,
            id="maintaining-temperature",
        ),
        pytest.param(
            encoded(temperature_rise_information(HeatingEventType="LEARNING")),
            STATE_UNKNOWN,
            id="learning",
        ),
        pytest.param(
            encoded(
                temperature_rise_information(
                    TargetEndDateTime="2026-10-01T09:10:00.0000000-04:00"
                )
            ),
            STATE_UNKNOWN,
            id="target-passed",
        ),
        pytest.param(
            encoded(temperature_rise_information(TargetEndDateTime=UNSET_DATETIME)),
            STATE_UNKNOWN,
            id="unset-target",
        ),
        pytest.param(
            encoded(temperature_rise_information(TargetEndDateTime=None)),
            STATE_UNKNOWN,
            id="no-target",
        ),
        pytest.param("[]", STATE_UNKNOWN, id="not-a-document"),
    ],
)
@pytest.mark.freeze_time(NOW)
@pytest.mark.usefixtures("setup_integration")
async def test_ready_time_sensor_matches_the_app(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
    raw_value: Any,
    expected_state: str,
) -> None:
    """The ready time is shown only when the vendor app shows one.

    That is, for a sampled, normal heat-up whose target is still ahead.
    """
    fake_client.set_status(
        TEST_BODY_OF_WATER_UUID, "TemperatureRiseInformation", raw_value
    )
    await hass.async_block_till_done()

    state = hass.states.get(READY_TIME_ENTITY_ID)
    assert state is not None
    assert state.state == expected_state


@pytest.mark.freeze_time(NOW)
@pytest.mark.usefixtures("setup_integration")
async def test_ready_time_sensor_warns_on_unparsable_document(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unparsable document is logged and shown as unknown."""
    fake_client.set_status(
        TEST_BODY_OF_WATER_UUID,
        "TemperatureRiseInformation",
        encoded(temperature_rise_information()),
    )
    await hass.async_block_till_done()
    fake_client.set_status(
        TEST_BODY_OF_WATER_UUID, "TemperatureRiseInformation", "not json"
    )
    await hass.async_block_till_done()

    state = hass.states.get(READY_TIME_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert "unparsable TemperatureRiseInformation" in caplog.text


@pytest.mark.freeze_time(NOW)
@pytest.mark.usefixtures("setup_integration")
async def test_ready_time_sensor_follows_revisions(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
) -> None:
    """Revised predictions update the sensor; clearing the field resets it."""
    fake_client.set_status(
        TEST_BODY_OF_WATER_UUID,
        "TemperatureRiseInformation",
        encoded(temperature_rise_information()),
    )
    await hass.async_block_till_done()
    fake_client.set_status(
        TEST_BODY_OF_WATER_UUID,
        "TemperatureRiseInformation",
        encoded(
            temperature_rise_information(
                TargetEndDateTime="2026-10-01T10:54:32.1000000-04:00"
            )
        ),
    )
    await hass.async_block_till_done()

    state = hass.states.get(READY_TIME_ENTITY_ID)
    assert state is not None
    assert state.state == "2026-10-01T14:54:32+00:00"

    fake_client.set_status(TEST_BODY_OF_WATER_UUID, "TemperatureRiseInformation", None)
    await hass.async_block_till_done()

    state = hass.states.get(READY_TIME_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.freeze_time(NOW)
@pytest.mark.usefixtures("setup_integration")
async def test_ready_time_sensor_clears_when_the_target_passes(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The ready time is cleared once it passes, even without a new push.

    Otherwise it would linger and show as "x minutes ago".
    """
    fake_client.set_status(
        TEST_BODY_OF_WATER_UUID,
        "TemperatureRiseInformation",
        encoded(temperature_rise_information()),
    )
    await hass.async_block_till_done()

    freezer.move_to("2026-10-01T10:56:00-04:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(READY_TIME_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.usefixtures("setup_integration")
async def test_site_mode_sensor_reflects_pushes(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
) -> None:
    """The mode sensor renders Mode pushes keyed by the site UUID.

    It must stay readable in INSTALLER mode - it's how the user sees why
    their controls are unavailable.
    """
    state = hass.states.get(MODE_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes[ATTR_OPTIONS] == ["normal", "installer", "fault", "factory"]

    fake_client.set_status(TEST_SITE_UUID, "Mode", "NORMAL")
    await hass.async_block_till_done()

    state = hass.states.get(MODE_ENTITY_ID)
    assert state is not None
    assert state.state == "normal"

    fake_client.set_status(TEST_SITE_UUID, "Mode", "INSTALLER")
    await hass.async_block_till_done()

    state = hass.states.get(MODE_ENTITY_ID)
    assert state is not None
    assert state.state == "installer"


@pytest.mark.usefixtures("setup_integration")
async def test_site_mode_sensor_ignores_unrecognized_modes(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
) -> None:
    """A Mode value this integration doesn't know renders as unknown."""
    fake_client.set_status(TEST_SITE_UUID, "Mode", "MAGMA")
    await hass.async_block_till_done()

    state = hass.states.get(MODE_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_no_mode_sensor_without_site_uuid(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_poolside_client: FakePoolsideClient,
) -> None:
    """Older firmware that reports no site UUID gets no mode sensor."""
    mock_poolside_client.site_uuid = None
    mock_poolside_client.async_get_control_layout.return_value = (
        PoolsideSite(uuid=None, name="Test Residence"),
        [make_control("heater-1", "Heater", ControlType.TEMPERATURE)],
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert set(hass.states.async_entity_ids("sensor")) == {
        ENTITY_ID,
        WATER_STATE_ENTITY_ID,
        DISABLED_REASON_ENTITY_ID,
    }


@pytest.mark.parametrize(
    ("raw_reasons", "expected_state"),
    [
        pytest.param('["WINTERIZED"]', "winterized", id="winterized"),
        pytest.param('["FREEZE_PROTECT"]', "freeze_protect", id="freeze-protect"),
        pytest.param('["cover-uuid-1"]', "pool_cover", id="pool-cover"),
        pytest.param(
            '["cover-uuid-1", "WINTERIZED"]', "winterized", id="winterized-wins"
        ),
        pytest.param("[]", "none", id="cleared"),
    ],
)
@pytest.mark.usefixtures("setup_integration")
async def test_disabled_reason_sensor_reflects_pushes(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
    raw_reasons: str,
    expected_state: str,
) -> None:
    """The disabled reason sensor renders DisabledReasons pushes.

    Unrecognized reasons are the UUID of the pool cover holding the control
    closed; WINTERIZED wins when several reasons are listed at once.
    """
    state = hass.states.get(DISABLED_REASON_ENTITY_ID)
    assert state is not None
    assert state.state == "none"
    assert state.attributes[ATTR_OPTIONS] == [
        "none",
        "winterized",
        "freeze_protect",
        "pool_cover",
    ]

    fake_client.set_status("heater-1", "DisabledReasons", raw_reasons)
    await hass.async_block_till_done()

    state = hass.states.get(DISABLED_REASON_ENTITY_ID)
    assert state is not None
    assert state.state == expected_state


@pytest.mark.usefixtures("setup_integration")
async def test_disabled_reason_reflects_layout_winterized_flag(
    hass: HomeAssistant,
) -> None:
    """A control winterized in the layout reports it from setup, while its own entity is unavailable."""
    state = hass.states.get("sensor.pool_cleaner_disabled_reason")
    assert state is not None
    assert state.state == "winterized"

    state = hass.states.get("switch.pool_cleaner")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("setup_integration")
async def test_unavailable_while_disconnected(
    hass: HomeAssistant,
    fake_client: FakePoolsideClient,
) -> None:
    """The sensors mirror the controller connection's availability."""
    fake_client.set_status(TEST_BODY_OF_WATER_UUID, "Temperature", 79)
    fake_client.set_connected(False)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    state = hass.states.get(DISABLED_REASON_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    fake_client.set_connected(True)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "79.0"
    state = hass.states.get(DISABLED_REASON_ENTITY_ID)
    assert state is not None
    assert state.state == "none"
