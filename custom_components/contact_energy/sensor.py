"""Contact Energy sensors."""

from datetime import datetime, timedelta

import logging
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, UnitOfEnergy
from homeassistant.components.sensor import SensorEntity

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)

from .api import ContactEnergyApi

from .const import (
    DOMAIN,
    SENSOR_USAGE_NAME,
    CONF_USAGE_DAYS,
)

NAME = DOMAIN
ISSUEURL = "https://github.com/AndrewTG5/hacs_contact_energy/issues"

STARTUP = f"""
-------------------------------------------------------------------
{NAME}
This is a custom component
If you have any issues with this you need to open an issue here:
{ISSUEURL}
-------------------------------------------------------------------
"""

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_USAGE_DAYS, default=5): cv.positive_int,
    }
)

SCAN_INTERVAL = timedelta(hours=3)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the platform async."""
    email = config.get(CONF_EMAIL)
    password = config.get(CONF_PASSWORD)

    usage_days = config.get(CONF_USAGE_DAYS)

    api = ContactEnergyApi(email, password)

    _LOGGER.debug("Setting up sensor(s)...")

    sensors = []
    sensors.append(ContactEnergyUsageSensor(SENSOR_USAGE_NAME, api, usage_days))
    async_add_entities(sensors, True)


class ContactEnergyUsageSensor(SensorEntity):
    """Define Contact Energy Usage sensor."""

    def __init__(self, name, api, usage_days):
        """Intialise the sensor."""
        self._name = name
        self._icon = "mdi:meter-electric"
        self._state = 0
        self._unit_of_measurement = "kWh"
        self._unique_id = DOMAIN
        self._device_class = "energy"
        self._state_class = "total"
        self._state_attributes = {}
        self._usage_days = usage_days
        self._api = api

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def state(self):
        """Return the state of the device."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the sensor."""
        return self._state_attributes

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def state_class(self):
        """Return the state class."""
        return self._state_class

    @property
    def device_class(self):
        """Return the device class."""
        return self._device_class

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._unique_id

    def update(self):
        """Begin usage update."""
        _LOGGER.debug("Beginning usage update")

        if self._api.login() is False:
            _LOGGER.error(
                "Failed to get past login (usage will not be updated) - check the username and password are valid"
            )
            return False

        # Get todays date
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        _LOGGER.debug("Fetching usage data")

        # Collect all data points with their timestamps and values
        all_data_points = []

        currency = 'NZD'

        last_dollar_value = 0.0  # Cache for previous dollarValue

        for i in range(self._usage_days):
            previous_day = today - timedelta(days=self._usage_days - i)
            response = self._api.get_usage(
                str(previous_day.year), str(previous_day.month), str(previous_day.day)
            )
            if response and response[0]:
                for point in response:
                    if point['currency'] and currency != point['currency']:
                        currency = point['currency']

                    if point["value"]:
                        # Use cached value if dollarValue is None
                        dollar_value = float(point["dollarValue"]) if point["dollarValue"] is not None else last_dollar_value
                        last_dollar_value = dollar_value  # Update cache
                        
                        all_data_points.append({
                            'timestamp': datetime.strptime(point["date"], "%Y-%m-%dT%H:%M:%S.%f%z"),
                            'value': float(point["value"]),
                            'dollarValue': dollar_value,
                            'is_paid': point["offpeakValue"] == "0.00"
                        })

        # Sort by timestamp to ensure correct order
        all_data_points.sort(key=lambda x: x['timestamp'])

        # Now build statistics with proper cumulative sums
        kWhStatistics = []
        kWhRunningSum = 0.0
        dollarStatistics = []
        dollarRunningSum = 0.0
        freeKWhStatistics = []
        freeKWhRunningSum = 0.0

        # Get the last known sums from existing statistics to continue from where we left off
        # This prevents negative values when statistics from older days have higher cumulative sums
        if all_data_points:
            first_timestamp = all_data_points[0]['timestamp']
            # Query statistics ending just before our data range
            existing_stats = statistics_during_period(
                self.hass,
                first_timestamp - timedelta(hours=2),
                first_timestamp - timedelta(seconds=1),
                {f"{DOMAIN}:energy_consumption", f"{DOMAIN}:energy_consumption_in_dollars", f"{DOMAIN}:free_energy_consumption"},
                "hour",
                None,
                {"sum"}
            )
            
            # Initialize running sums from the last known values
            if f"{DOMAIN}:energy_consumption" in existing_stats and existing_stats[f"{DOMAIN}:energy_consumption"]:
                last_stat = existing_stats[f"{DOMAIN}:energy_consumption"][-1]
                if last_stat.get("sum") is not None:
                    kWhRunningSum = last_stat["sum"]
                    _LOGGER.debug("Starting kWh sum from existing statistics: %s", kWhRunningSum)
            
            if f"{DOMAIN}:energy_consumption_in_dollars" in existing_stats and existing_stats[f"{DOMAIN}:energy_consumption_in_dollars"]:
                last_stat = existing_stats[f"{DOMAIN}:energy_consumption_in_dollars"][-1]
                if last_stat.get("sum") is not None:
                    dollarRunningSum = last_stat["sum"]
                    _LOGGER.debug("Starting dollar sum from existing statistics: %s", dollarRunningSum)
            
            if f"{DOMAIN}:free_energy_consumption" in existing_stats and existing_stats[f"{DOMAIN}:free_energy_consumption"]:
                last_stat = existing_stats[f"{DOMAIN}:free_energy_consumption"][-1]
                if last_stat.get("sum") is not None:
                    freeKWhRunningSum = last_stat["sum"]
                    _LOGGER.debug("Starting free kWh sum from existing statistics: %s", freeKWhRunningSum)

        for point in all_data_points:
            # If the off peak value is '0.00' then the energy is paid.
            # HASSIO statistics requires us to add values as a sum of all previous values.
            if point['is_paid']:
                kWhRunningSum = kWhRunningSum + point['value']
                dollarRunningSum = dollarRunningSum + point['dollarValue']
            else:
                freeKWhRunningSum = freeKWhRunningSum + point['value']

            freeKWhStatistics.append(
                StatisticData(
                    start=point['timestamp'],
                    sum=freeKWhRunningSum,
                )
            )
            kWhStatistics.append(
                StatisticData(
                    start=point['timestamp'],
                    sum=kWhRunningSum,
                )
            )
            dollarStatistics.append(
                StatisticData(
                    start=point['timestamp'],
                    sum=dollarRunningSum,
                )
            )

        kWhMetadata = StatisticMetaData(
            mean_type=0,
            has_sum=True,
            unit_class=None,
            name="ContactEnergy",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:energy_consumption",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
        async_add_external_statistics(self.hass, kWhMetadata, kWhStatistics)

        dollarMetadata = StatisticMetaData(
            mean_type=0,
            has_sum=True,
            unit_class=None,
            name="ContactEnergyDollars",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:energy_consumption_in_dollars",
            unit_of_measurement=currency,
        )
        async_add_external_statistics(self.hass, dollarMetadata, dollarStatistics)

        freeKWHMetadata = StatisticMetaData(
            mean_type=0,
            has_sum=True,
            unit_class=None,
            name="FreeContactEnergy",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:free_energy_consumption",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
        async_add_external_statistics(self.hass, freeKWHMetadata, freeKWhStatistics)

        freeEnergyDollarMetadata = StatisticMetaData(
            mean_type=0,
            has_sum=True,
            unit_class=None,
            name="FreeContactEnergyDollars",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:free_energy_consumption_in_dollars",
            unit_of_measurement=currency,
        )
        async_add_external_statistics(self.hass, freeEnergyDollarMetadata, [])