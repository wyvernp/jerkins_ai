"""Config flow for Jerkins AI integration."""
import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector, area_registry
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.const import (
    CONF_NAME,
    CONF_URL,
)

from .const import (
    DOMAIN,
    DEFAULT_NAME,
    CONF_SENSORS,
    CONF_AREA_MAPPINGS,
    CONF_ACTION_MAPPINGS,
    CONF_POLLING_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    STEP_USER,
    STEP_SENSORS,
    STEP_AREAS,
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
    """Get all sensor and binary_sensor entities."""
    states = hass.states.async_all()
    sensor_options = []
    
    for state in states:
        # Include both sensors and binary sensors
        if state.domain == "sensor" or state.domain == "binary_sensor":
            sensor_options.append({
                "value": state.entity_id,
                "label": f"{state.name} ({state.entity_id})"
            })
    
    return sensor_options


def get_area_list(hass: HomeAssistant) -> List[dict]:
    """Get all areas in Home Assistant."""
    ar_registry = area_registry.async_get(hass)
    area_options = []
    
    for area_id, area in ar_registry.areas.items():
        area_options.append({
            "value": area_id,
            "label": area.name
        })
    
    # Add a custom area option
    area_options.append({
        "value": "custom",
        "label": "Custom Area (enter name)"
    })
    
    return area_options


def get_entity_area(hass: HomeAssistant, entity_id: str) -> Optional[str]:
    """Get the area ID for an entity."""
    try:
        # Method 1: Check entity registry for direct area assignment
        entity_registry = async_get_entity_registry(hass)
        entity_entry = entity_registry.async_get(entity_id)
        
        if entity_entry and entity_entry.area_id:
            _LOGGER.debug("Entity %s has direct area assignment: %s", entity_id, entity_entry.area_id)
            return entity_entry.area_id
        
        # Method 2: Check if entity's device has an area assigned
        if entity_entry and entity_entry.device_id:
            device_registry = hass.helpers.device_registry.async_get(hass)
            device = device_registry.async_get(entity_entry.device_id)
            if device and device.area_id:
                _LOGGER.debug("Entity %s has area via device %s: %s", 
                             entity_id, device.id, device.area_id)
                return device.area_id
        
        # Method 3: Check if entity name contains an area name
        # This is a fallback for entities not properly registered
        area_reg = area_registry.async_get(hass)
        state = hass.states.get(entity_id)
        
        if state and state.name:
            # Check if any area name is in the entity name or entity_id
            entity_name = state.name.lower()
            entity_id_lower = entity_id.lower()
            
            for area_id, area in area_reg.areas.items():
                area_name = area.name.lower()
                # Check for area name match in entity name or ID
                if (area_name in entity_name or 
                    area_name.replace(' ', '_') in entity_id_lower or
                    area_name.replace(' ', '') in entity_id_lower):
                    _LOGGER.debug("Entity %s matched to area %s by name", entity_id, area.name)
                    return area_id
        
        # Method 4: For manually created entities without area 
        # assignments directly in their config, try to infer from attributes
        if state and state.attributes:
            if 'device_class' in state.attributes and 'room' in entity_id_lower:
                # Extract room name from entity_id for sensor.bedroom_temperature
                parts = entity_id_lower.split('_')
                if len(parts) > 1:
                    potential_room = parts[0].split('.')[1]  # Remove domain
                    # Check if this room name corresponds to an area
                    for area_id, area in area_reg.areas.items():
                        if potential_room in area.name.lower():
                            _LOGGER.debug("Entity %s matched to area %s by room inference", 
                                         entity_id, area.name)
                            return area_id
        
        # Log that we couldn't find an area
        _LOGGER.debug("Could not determine area for entity %s", entity_id)
        return None
        
    except Exception as e:
        _LOGGER.warning("Error getting area for entity %s: %s", entity_id, e)
        import traceback
        _LOGGER.debug("Traceback: %s", traceback.format_exc())
        return None


def get_entities_in_area(hass: HomeAssistant, area_id: str) -> List[str]:
    """Get all entities in a specific area."""
    entity_registry = async_get_entity_registry(hass)
    area_entities = []
    
    # First, get entities directly assigned to this area
    for entity_id, entry in entity_registry.entities.items():
        if entry.area_id == area_id:
            area_entities.append(entity_id)
    
    # Then get entities whose devices are in this area
    device_registry = hass.helpers.device_registry.async_get()
    for device_id, device in device_registry.devices.items():
        if device.area_id == area_id:
            # Find entities attached to this device
            for entity_id, entry in entity_registry.entities.items():
                if entry.device_id == device_id and entity_id not in area_entities:
                    area_entities.append(entity_id)
    
    return area_entities


def get_services_for_area(hass: HomeAssistant, area_id: str) -> List[dict]:
    """Get all available services for entities in an area."""
    service_options = []
    area_entities = []
    
    # Get all entities in this area
    if area_id == "custom":
        # For custom areas, we can't get entities (yet)
        pass
    else:
        area_entities = get_entities_in_area(hass, area_id)
    
    # Generate service options based on discovered entities
    service_map = {}
    
    for entity_id in area_entities:
        # Skip if we can't get the entity state
        state = hass.states.get(entity_id)
        if not state:
            continue
            
        domain = entity_id.split(".", 1)[0]
        
        # Only include supported domains
        if domain not in SUPPORTED_DOMAINS:
            continue
        
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
    
    # If no entities were found or this is a custom area, add some default services
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
        self._area_mappings = {}
        self._action_mappings = {}
        self._current_sensor = None
        self._current_area = None

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
            selected_sensors = user_input.get(CONF_SENSORS, [])
            if not selected_sensors:
                errors["base"] = "no_sensors"
            else:
                # Store the selected sensors
                self._sensors = selected_sensors
                self._data[CONF_SENSORS] = self._sensors
                
                # Create default area mappings (use "default" area for all)
                self._area_mappings = {sensor_id: "default" for sensor_id in self._sensors}
                self._data[CONF_AREA_MAPPINGS] = self._area_mappings
                
                # Set up for the actions step
                self._unique_areas = ["default"]
                self._current_area = "default"
                
                # Skip directly to actions step
                return await self.async_step_actions()
        
        # Get all sensors without filtering by area
        sensor_options = []
        states = self.hass.states.async_all()
        
        for state in states:
            # Include both sensors and binary sensors
            if state.domain == "sensor" or state.domain == "binary_sensor":
                entity_id = state.entity_id
                sensor_options.append({
                    "value": entity_id,
                    "label": f"{state.name} ({entity_id})"
                })
        
        if not sensor_options:
            return self.async_abort(reason="no_sensors_available")
        
        _LOGGER.info("Found %d sensors", len(sensor_options))
        
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

    async def async_step_areas(self, user_input=None) -> FlowResult:
        """Handle the area assignment step."""
        errors = {}
        
        if user_input is not None:
            area = user_input.get("area")
            custom_area = user_input.get("custom_area")
            
            # Handle custom area
            if area == "custom" and custom_area:
                area = f"custom.{custom_area}"
            
            if not area:
                errors["base"] = "no_area"
            else:
                # Store area mapping for current sensor
                self._area_mappings[self._current_sensor] = area
                
                # Find the next sensor without an area
                next_sensor = None
                for sensor_id in self._sensors:
                    if sensor_id not in self._area_mappings:
                        next_sensor = sensor_id
                        break
                
                if next_sensor:
                    self._current_sensor = next_sensor
                    return await self.async_step_areas()
                else:
                    # All sensors have areas, move to actions
                    self._data[CONF_AREA_MAPPINGS] = self._area_mappings
                    self._unique_areas = list(set(self._area_mappings.values()))
                    self._current_area = self._unique_areas[0]
                    return await self.async_step_actions()
        
        area_options = get_area_list(self.hass)
        
        # Get the friendly name of the current sensor for the UI
        sensor_state = self.hass.states.get(self._current_sensor)
        sensor_name = self._current_sensor
        if sensor_state and sensor_state.name:
            sensor_name = f"{sensor_state.name} ({self._current_sensor})"
            
        description_placeholders = {"sensor": sensor_name}
        
        schema = vol.Schema({
            vol.Required("area"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=area_options,
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        })
        
        # Add custom area field that is conditionally shown
        schema = schema.extend({
            vol.Optional("custom_area"): str,
        })
        
        return self.async_show_form(
            step_id=STEP_AREAS,
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
                # Store actions for current area
                self._action_mappings[self._current_area] = actions
                
                # Move to next area or to LLM step
                area_index = self._unique_areas.index(self._current_area)
                if area_index < len(self._unique_areas) - 1:
                    self._current_area = self._unique_areas[area_index + 1]
                    return await self.async_step_actions()
                else:
                    # Store action mappings and move to LLM configuration
                    self._data[CONF_ACTION_MAPPINGS] = self._action_mappings
                    return await self.async_step_llm()
        
        # Dynamically discover services for this area
        service_options = get_services_for_area(self.hass, self._current_area)
        
        # Get area name for display
        area_name = self._current_area
        if self._current_area.startswith("custom."):
            area_name = self._current_area.split(".", 1)[1]
        else:
            try:
                ar_registry = area_registry.async_get(self.hass)
                area_obj = ar_registry.async_get_area(self._current_area)
                if area_obj:
                    area_name = area_obj.name
            except Exception:
                pass
                
        description_placeholders = {"area": area_name}
        
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