"""Config flow for Jerkins AI integration."""
import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.const import (
    CONF_NAME,
    CONF_URL,
)

from .const import (
    DOMAIN,
    DEFAULT_NAME,
    CONF_SENSORS,
    CONF_ZONE_MAPPINGS,
    CONF_ACTION_MAPPINGS,
    CONF_POLLING_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    STEP_USER,
    STEP_SENSORS,
    STEP_ZONES,
    STEP_ACTIONS,
    STEP_LLM,
    SUPPORTED_DOMAINS,
    SERVICE_CATEGORY_TOGGLE,
    SERVICE_CATEGORY_COVER,
    SERVICE_CATEGORY_CLIMATE,
    SERVICE_CATEGORY_MEDIA,
)

_LOGGER = logging.getLogger(__name__)


def get_sensor_entities(hass: HomeAssistant) -> List[dict]:
    """Get all sensor entities."""
    states = hass.states.async_all()
    sensor_options = []
    
    for state in states:
        if state.domain == "sensor":
            sensor_options.append({
                "value": state.entity_id,
                "label": f"{state.name} ({state.entity_id})"
            })
    
    return sensor_options


def get_zone_entities(hass: HomeAssistant) -> List[dict]:
    """Get all zone entities."""
    states = hass.states.async_all()
    zone_options = []
    
    for state in states:
        if state.domain == "zone":
            zone_options.append({
                "value": state.entity_id,
                "label": f"{state.name} ({state.entity_id})"
            })
    
    # Add options for standard rooms if no zones are defined
    if not zone_options:
        for room in ["living_room", "kitchen", "bedroom", "bathroom", "office"]:
            zone_options.append({
                "value": f"room.{room}",
                "label": f"{room.replace('_', ' ').title()}"
            })
    
    # Add a custom zone option
    zone_options.append({
        "value": "custom",
        "label": "Custom Zone (enter name)"
    })
    
    return zone_options


def get_area_entities(hass: HomeAssistant) -> Dict[str, List[str]]:
    """Get all entities grouped by area/room."""
    area_entities = {}
    
    # Get all registry entries
    entity_registry = async_get_entity_registry(hass)
    
    # Get areas registry
    try:
        area_registry = hass.helpers.area_registry.async_get(hass)
        
        # Group entities by area
        for entity_id, entry in entity_registry.entities.items():
            if entry.area_id and entry.area_id in area_registry.areas:
                area_name = area_registry.areas[entry.area_id].name
                if area_name not in area_entities:
                    area_entities[area_name] = []
                area_entities[area_name].append(entity_id)
                
    except Exception as e:
        _LOGGER.warning("Error accessing area registry: %s", e)
    
    return area_entities


def get_services_for_zone(hass: HomeAssistant, zone_id: str) -> List[dict]:
    """Get all available services for entities in a zone."""
    service_options = []
    zone_entities = []
    
    # First check if this is a real zone entity or a custom zone
    if zone_id.startswith("zone."):
        # Real zone - find entities in this zone
        try:
            # This is a simplified approach - in a real implementation, you would
            # use the zone's GPS coordinates and compare with device_tracker entities
            pass
        except Exception:
            _LOGGER.warning("Error finding entities in zone %s", zone_id)
    
    # Check if this is a room
    elif zone_id.startswith("room."):
        room_name = zone_id.split(".", 1)[1].replace("_", " ").title()
        
        # Try to find entities in this room by area name
        area_entities = get_area_entities(hass)
        for area_name, entities in area_entities.items():
            if room_name.lower() in area_name.lower():
                zone_entities.extend(entities)
    
    # If we couldn't find entities specifically for this zone,
    # look for entities that might have the zone name in their entity_id or name
    if not zone_entities:
        zone_name = zone_id.split(".", 1)[1] if "." in zone_id else zone_id
        
        states = hass.states.async_all()
        for state in states:
            # Only include supported domains
            if state.domain not in SUPPORTED_DOMAINS:
                continue
                
            # Check if entity might be in this zone
            entity_name = state.name.lower() if state.name else ""
            entity_id = state.entity_id.lower()
            
            # Match if zone name appears in entity ID or friendly name
            if (zone_name.lower().replace("_", "") in entity_id or 
                zone_name.lower().replace("_", " ") in entity_name):
                zone_entities.append(state.entity_id)
    
    # Generate service options based on discovered entities
    service_map = {}
    
    for entity_id in zone_entities:
        domain = entity_id.split(".", 1)[0]
        
        # Standard services based on domain
        if domain in SERVICE_CATEGORY_TOGGLE:
            for service in ["turn_on", "turn_off", "toggle"]:
                service_id = f"{domain}.{service}"
                if service_id not in service_map:
                    service_name = f"{service.replace('_', ' ').title()} {domain.replace('_', ' ').title()}"
                    service_map[service_id] = service_name
        
        elif domain in SERVICE_CATEGORY_COVER:
            for service in ["open_cover", "close_cover", "set_cover_position"]:
                service_id = f"{domain}.{service}"
                if service_id not in service_map:
                    service_name = f"{service.replace('_', ' ').title().replace('Cover', '')}"
                    service_map[service_id] = service_name
        
        elif domain in SERVICE_CATEGORY_CLIMATE:
            for service in ["set_temperature", "set_hvac_mode"]:
                service_id = f"{domain}.{service}"
                if service_id not in service_map:
                    service_name = f"{service.replace('_', ' ').title().replace('Hvac', 'HVAC')}"
                    service_map[service_id] = service_name
        
        elif domain in SERVICE_CATEGORY_MEDIA:
            for service in ["play_media", "media_play", "media_pause", "media_stop", "volume_set"]:
                service_id = f"{domain}.{service}"
                if service_id not in service_map:
                    service_name = f"{service.replace('_', ' ').title().replace('Media', '')}"
                    service_map[service_id] = service_name
    
    # If no entities were found, add some default services that might be useful
    if not service_map:
        default_services = {
            "light.turn_on": "Turn On Lights",
            "light.turn_off": "Turn Off Lights",
            "switch.turn_on": "Turn On Switch",
            "switch.turn_off": "Turn Off Switch",
        }
        service_map.update(default_services)
    
    # Convert the map to selector options
    for service_id, service_name in service_map.items():
        service_options.append({
            "value": service_id,
            "label": f"{service_name} ({service_id})"
        })
    
    # Add a custom action option
    service_options.append({
        "value": "custom",
        "label": "Custom Action (enter name)"
    })
    
    return service_options


class JerkinsAIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Jerkins AI."""

    VERSION = 1
    
    def __init__(self):
        """Initialize the config flow."""
        self._data = {}
        self._sensors = []
        self._zone_mappings = {}
        self._action_mappings = {}
        self._current_sensor = None
        self._current_zone = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id=STEP_USER,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_POLLING_INTERVAL, default=DEFAULT_POLLING_INTERVAL): int,
                }
            ),
        )

    async def async_step_sensors(self, user_input=None) -> FlowResult:
        """Handle the sensor selection step."""
        errors = {}
        
        if user_input is not None:
            self._sensors = user_input.get(CONF_SENSORS, [])
            if not self._sensors:
                errors["base"] = "no_sensors"
            else:
                # Store sensors and move to zone assignment
                self._data[CONF_SENSORS] = self._sensors
                self._current_sensor = self._sensors[0]
                return await self.async_step_zones()
        
        sensor_options = get_sensor_entities(self.hass)
        
        return self.async_show_form(
            step_id=STEP_SENSORS,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SENSORS): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=sensor_options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_zones(self, user_input=None) -> FlowResult:
        """Handle the zone assignment step."""
        errors = {}
        
        if user_input is not None:
            zone = user_input.get("zone")
            custom_zone = user_input.get("custom_zone")
            
            # Handle custom zone
            if zone == "custom" and custom_zone:
                zone = custom_zone
            
            if not zone:
                errors["base"] = "no_zone"
            else:
                # Store zone mapping for current sensor
                self._zone_mappings[self._current_sensor] = zone
                
                # Move to next sensor or to actions step
                sensor_index = self._sensors.index(self._current_sensor)
                if sensor_index < len(self._sensors) - 1:
                    self._current_sensor = self._sensors[sensor_index + 1]
                    return await self.async_step_zones()
                else:
                    # Store zone mappings and move to actions
                    self._data[CONF_ZONE_MAPPINGS] = self._zone_mappings
                    # Get unique zones for action mapping
                    self._unique_zones = list(set(self._zone_mappings.values()))
                    self._current_zone = self._unique_zones[0]
                    return await self.async_step_actions()
        
        zone_options = get_zone_entities(self.hass)
        description_placeholders = {"sensor": self._current_sensor}
        
        schema = vol.Schema({
            vol.Required("zone"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=zone_options,
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        })
        
        # Add custom zone field that is conditionally shown
        schema = schema.extend({
            vol.Optional("custom_zone"): str,
        })
        
        return self.async_show_form(
            step_id=STEP_ZONES,
            data_schema=schema,
            description_placeholders=description_placeholders,
            errors=errors,
        )

    async def async_step_actions(self, user_input=None) -> FlowResult:
        """Handle the action configuration step."""
        errors = {}
        
        if user_input is not None:
            actions = user_input.get("actions", [])
            custom_actions = user_input.get("custom_actions", "")
            
            # Process custom actions if any
            if custom_actions:
                custom_action_list = [action.strip() for action in custom_actions.split(",")]
                actions.extend(custom_action_list)
            
            # Remove "custom" from actions if present
            if "custom" in actions:
                actions.remove("custom")
            
            if not actions:
                errors["base"] = "no_actions"
            else:
                # Store actions for current zone
                self._action_mappings[self._current_zone] = actions
                
                # Move to next zone or to LLM step
                zone_index = self._unique_zones.index(self._current_zone)
                if zone_index < len(self._unique_zones) - 1:
                    self._current_zone = self._unique_zones[zone_index + 1]
                    return await self.async_step_actions()
                else:
                    # Store action mappings and move to LLM configuration
                    self._data[CONF_ACTION_MAPPINGS] = self._action_mappings
                    return await self.async_step_llm()
        
        # Dynamically discover services for this zone
        service_options = get_services_for_zone(self.hass, self._current_zone)
        description_placeholders = {"zone": self._current_zone}
        
        schema = vol.Schema({
            vol.Required("actions"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=service_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        })
        
        # Add custom actions field that is conditionally shown
        schema = schema.extend({
            vol.Optional("custom_actions"): str,
        })
        
        return self.async_show_form(
            step_id=STEP_ACTIONS,
            data_schema=schema,
            description_placeholders=description_placeholders,
            errors=errors,
        )

    async def async_step_llm(self, user_input=None) -> FlowResult:
        """Handle the LLM URL configuration step."""
        errors = {}
        
        if user_input is not None:
            url = user_input.get(CONF_URL)
            
            if not url:
                errors[CONF_URL] = "invalid_url"
            else:
                # Store LLM URL and create entry
                self._data[CONF_URL] = url
                return self.async_create_entry(
                    title=self._data.get(CONF_NAME, DEFAULT_NAME),
                    data=self._data,
                )
        
        return self.async_show_form(
            step_id=STEP_LLM,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL): str,
                }
            ),
            errors=errors,
        )