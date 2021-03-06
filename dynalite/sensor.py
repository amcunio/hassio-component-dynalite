import logging
import re
import json
import time

import voluptuous as vol

from homeassistant.helpers.entity import Entity

import homeassistant.components.mqtt as mqtt
from homeassistant.components.mqtt import (
    CONF_STATE_TOPIC, CONF_COMMAND_TOPIC, CONF_QOS, CONF_RETAIN)

from homeassistant.const import (
    ATTR_ENTITY_ID, CONF_HOST, CONF_ICON, CONF_PORT, CONF_NAME, SERVICE_TURN_OFF, SERVICE_TURN_ON,
    SERVICE_TOGGLE, STATE_ON, STATE_OFF, STATE_STANDBY)
from homeassistant.loader import bind_hass
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA

_LOGGER = logging.getLogger(__name__)

# The domain of your component. Should be equal to the name of your component.
DOMAIN = "dynalite"

CONF_LOGLEVEL = 'log_level'
CONF_AREA = 'area'
CONF_PRESET = 'preset'
CONF_NODEFAULT = 'nodefault'
CONF_FADE = 'fade'
CONF_DEFAULT = 'default'
CONF_MQTT_DISCOVERY_TOPIC = 'discovery_topic'
CONF_MQTT_DEVICE_TOPIC = 'device_topic'
CONF_MQTT_QOS = 'qos'

DEFAULT_NAME = 'dynalite'
DEFAULT_PORT = 12345
DEFAULT_LOGGING = 'info'
DEFAULT_DISCOVERY_TOPIC = 'homeassistant'
DEFAULT_DEVICE_TOPIC = DEFAULT_NAME
DEFAULT_MQTT_QOS = '0'
DEFAULT_ICON = 'mdi:lightbulb-outline'

PRESET_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_FADE): cv.string
})

PRESET_SCHEMA = vol.Schema({
    cv.slug: vol.Any(PRESET_DATA_SCHEMA, None)
})

AREA_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_FADE): cv.string,
    vol.Optional(CONF_NODEFAULT): cv.boolean,
    vol.Optional(CONF_PRESET): PRESET_SCHEMA
})

AREA_SCHEMA = vol.Schema({
    cv.slug: vol.Any(AREA_DATA_SCHEMA, None)
})

PLATFORM_DEFAULTS_SCHEMA = vol.Schema({
    vol.Optional(CONF_FADE): cv.string
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_LOGLEVEL, default=DEFAULT_LOGGING): cv.string,
    vol.Optional(CONF_MQTT_DISCOVERY_TOPIC, default=DEFAULT_DISCOVERY_TOPIC): cv.string,
    vol.Optional(CONF_MQTT_DEVICE_TOPIC, default=DEFAULT_DEVICE_TOPIC): cv.string,
    vol.Optional(CONF_AREA): AREA_SCHEMA,
    vol.Optional(CONF_ICON, default=DEFAULT_ICON): cv.string,
    vol.Optional(CONF_MQTT_QOS, default=DEFAULT_MQTT_QOS): cv.string,
    vol.Optional(CONF_DEFAULT): PLATFORM_DEFAULTS_SCHEMA,
    vol.Optional(CONF_PRESET): PRESET_SCHEMA
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Dynalite MQTT Gateway Sensor."""
    add_devices([DynaliteSensor(hass, dict(config))])


class DiscoveryPayload(object):

    def __init__(self, topic=None, mqttName=None, lightName=None):
        self.platform = 'mqtt'
        self.name = None
        self.qos = 0
        self.payload_on = 'ON'
        self.payload_off = 'OFF'
        self.optimistic = False

        if topic is not None:
            self.state_topic = topic + '/'
            self.command_topic = topic + '/'
            self.brightness_state_topic = topic + '/'
            self.brightness_command_topic = topic + '/'
        else:
            self.state_topic = 'dynalite/'
            self.command_topic = 'dynalite/'
            self.brightness_state_topic = 'dynalite/'
            self.brightness_command_topic = 'dynalite/'

        if mqttName is None and lightName is None:
            mqttName = 'dyn_unknown'
        elif mqttName is None and lightName is not None:
            mqttName = lightName.replace(" ", "_").lower()

        # if lightName is not None:
        #     self.friendly_name = lightName
        # else:
        #     self.friendly_name = "Unknown Light"

        if lightName is not None:
            self.name = lightName
        else:
            self.name = "Unknown Light"

        self.state_topic += mqttName + '/status'
        self.command_topic += mqttName + '/switch'
        self.brightness_state_topic += mqttName + '/brightness'
        self.brightness_command_topic += mqttName + '/brightness/set'

    def getPayload(self):
        return json.dumps(self, default=lambda o: o.__dict__,
                          sort_keys=True)

    def __repr__(self):
        return str(self.__dict__)


class DynaliteSensor(Entity):
    """Dynalite to MQTT Sensor."""

    def __init__(self, hass, config):
        """Initialize the gateway."""
        from dynalite_lib import Dynalite
        alphaonly = re.compile('[\W_]+')
        self.hass = hass
        self._name = config[CONF_NAME]
        self._icon = config[CONF_ICON]
        self._qos = int(config[CONF_MQTT_QOS])
        self._lastMQTTOut = None
        self._lastMQTTIn = None
        self._lastDynetOut = None
        self._lastDynetIn = None
        self._discoveryTopic = config['discovery_topic']
        self._mqttTopic = config['device_topic']
        self._dynalite = Dynalite(config=config, loop=hass.loop)
        eventHandler = self._dynalite.addListener(
            listenerFunction=self.handleEvent)
        newPresetHandler = self._dynalite.addListener(
            listenerFunction=self.handleNewPreset)
        presetChangeHandler = self._dynalite.addListener(
            listenerFunction=self.handlePresetChange)
        eventHandler.monitorEvent('*')
        newPresetHandler.monitorEvent('NEWPRESET')
        presetChangeHandler.monitorEvent('PRESET')
        mqtt.subscribe(hass, self._mqttTopic + '/#',
                       self.mqttReceived, self._qos)
        self._dynalite.start()
        self._state = 'Connected'

    @property
    def should_poll(self):
        """If entity should be polled."""
        return False

    @property
    def name(self):
        """Return name of the boolean input."""
        return self._name

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        if self._state is None or self._state == False:
            return "mdi:close-circle-outline"
        else:
            return self._icon

    @property
    def is_on(self):
        """Return true if entity is on."""
        if self._state is not None:
            return True
        else:
            return False

    @property
    def device_state_attributes(self):
        """Return the state attributes of the sensor."""
        attr = {}
        attr['LastMQTTIn'] = self._lastMQTTIn
        attr['lastMQTTOut'] = self._lastMQTTOut
        attr['LastDynetIn'] = self._lastDynetIn
        attr['LastDynetOut'] = self._lastDynetOut
        return attr

    @property
    def state(self):
        return self._state

    def handleEvent(self, event=None, dynalite=None):
        self._lastDynetIn = {
            'event': event.toJson(),
            'ts': time.time()
        }
        self.schedule_update_ha_state()

    def getMQTTName(self, area=None, preset=None):
        if area is None or preset is None:
            return
        return 'dyn_area_' + str(area) + '_preset_' + str(preset)

    def MQTTNameToAreaPreset(self, mqttName=None):
        if mqttName is None:
            return
        rx = r"^.*_area_(?P<area>\d+).*_preset_(?P<preset>\d+).*"
        m = re.search(rx, mqttName.lower())
        if not m:
            return
        areaPreset = m.groupdict()
        if (areaPreset['area'] and areaPreset['preset']):
            areaPreset['area'] = int(areaPreset['area'])
            areaPreset['preset'] = int(areaPreset['preset'])
            return areaPreset

    def handleNewPreset(self, event=None, dynalite=None):
        if not hasattr(event, 'data'):
            return
        if not 'area' in event.data:
            return
        if not 'preset' in event.data:
            return
        if not 'name' in event.data:
            return
        mqttName = self.getMQTTName(
            area=event.data['area'], preset=event.data['preset'])
        discoveryTopic = self._discoveryTopic + '/light/' + mqttName + '/config'
        payload = DiscoveryPayload(
            topic=self._mqttTopic, mqttName=mqttName, lightName=event.data['name']).getPayload()
        payloadBytes = str.encode(payload)
        self.mqttPublish(topic=discoveryTopic, payload=payloadBytes)

    def handlePresetChange(self, event=None, dynalite=None):
        topic = self._mqttTopic + '/' + \
            self.getMQTTName(
                area=event.data['area'], preset=event.data['preset']) + '/status'
        self.mqttPublish(topic=topic, payload=event.data['state'])

    def allDevicesCommand(self, command):
        if command == 'STATE':
            self._dynalite.state()

    def mqttPublish(self, topic=None, payload=None, qos=None, retain=None):
        if topic is None:
            topic = self._mqttTopic + '/unknown'
        if payload is None:
            payload = ""
        else:
            if isinstance(payload, bytes):
                payload = payload.decode()
        if qos is None:
            qos = self._qos
        if retain is None:
            retain = False
        if mqtt:
            mqtt.publish(hass=self.hass, topic=topic,
                         payload=payload, qos=qos, retain=retain)
            self._lastMQTTOut = {
                'topic': topic,
                'payload': payload,
                'ts': time.time()
            }
        else:
            _LOGGER.error("No MQTT Send to %s. MQTT is not available." % topic)
        self.schedule_update_ha_state()

    async def async_added_to_hass(self):
        """Call when entity about to be added to hass."""
        # If not None, we got an initial value.
        if self._state is not None:
            return

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        self._state = True
        await self.async_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        self._state = False
        await self.async_update_ha_state()

    def mqttReceived(self, topic, payload, qos):
        """A new MQTT message has been received."""
        self._lastMQTTIn = {
            'topic': topic,
            'payload': payload,
            'ts': time.time()
        }
        if not topic.startswith(self._mqttTopic + '/'):
            _LOGGER.warning("Ignoring topic %s" % topic)
            return
        event = topic[len(self._mqttTopic) + 1:].split('/')
        if len(event) != 2:
            _LOGGER.warning("Unknown MQTT Topic: %s (%s)" % (topic, event))
            return
        device = event[0].upper()
        command = event[1].upper()
        if device == 'DEVICES':
            self.allDevicesCommand(command=command)
        else:
            areaPreset = self.MQTTNameToAreaPreset(device)
            if areaPreset and command == 'SWITCH':
                area = areaPreset['area']
                preset = areaPreset['preset']
                if payload == 'ON':
                    self._dynalite.devices['area'][area].preset[preset].turnOn(
                        sendDynet=True, sendMQTT=True)
                elif payload == 'OFF':
                    self._dynalite.devices['area'][area].preset[preset].turnOn(
                        sendDynet=True, sendMQTT=True)
        self.schedule_update_ha_state()
