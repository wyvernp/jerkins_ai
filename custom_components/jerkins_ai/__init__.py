"""The Jerkins AI integration."""
import asyncio
import logging
from datetime import timedelta

import aiohttp
import async_timeout
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.entity_registry import async_get
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import area_registry
from homeassistant.const import (
    CONF_URL,
)
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    CONF_SENSORS,
    CONF_AREA_MAPPINGS,
    CONF_ACTION_MAPPINGS,
    CONF_POLLING_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    SUPPORTED_DOMAINS,
)

_LOGGER = logging.getLogger(__name__)

# Service schemas
FORCE_UPDATE_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): cv.string,
})

UPDATE_AREA_MAPPINGS_SCHEMA = vol.Schema({
    vol.Required("entry_id"): cv.string,
    vol.Required("sensor_id"): cv.entity_id,
    vol.Required("area_id"): cv.string,
})

UPDATE_ACTION_MAPPINGS_SCHEMA = vol.Schema({
    vol.Required("entry_id"): cv.string,
    vol.Required("area_id"): cv.string,
    vol.Required("actions"): cv.string,
})

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jerkins AI from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    jerkins_ai = JerkinsAI(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = jerkins_ai
    
    await jerkins_ai.async_setup()
    
    # Register services
    async def force_update_service(call: ServiceCall) -> None:
        """Handle the service call to force an update."""
        entry_id = call.data.get("entry_id")
        
        if entry_id:
            if entry_id in hass.data[DOMAIN]:
                await hass.data[DOMAIN][entry_id].async_update()
            else:
                _LOGGER.error("Config entry %s not found", entry_id)
        else:
            # Update all instances
            for jerkins_instance in hass.data[DOMAIN].values():
                await jerkins_instance.async_update()
    
    async def update_area_mappings_service(call: ServiceCall) -> None:
        """Handle the service call to update area mappings."""
        entry_id = call.data.get("entry_id")
        sensor_id = call.data.get("sensor_id")
        area_id = call.data.get("area_id")
        
        if entry_id in hass.data[DOMAIN]:
            jerkins_instance = hass.data[DOMAIN][entry_id]
            # Update the area mapping
            await jerkins_instance.async_update_area_mapping(sensor_id, area_id)
        else:
            _LOGGER.error("Config entry %s not found", entry_id)
    
    async def update_action_mappings_service(call: ServiceCall) -> None:
        """Handle the service call to update action mappings."""
        entry_id = call.data.get("entry_id")
        area_id = call.data.get("area_id")
        actions_str = call.data.get("actions")
        
        # Parse actions string into a list
        actions = [action.strip() for action in actions_str.split(",") if action.strip()]
        
        if entry_id in hass.data[DOMAIN]:
            jerkins_instance = hass.data[DOMAIN][entry_id]
            # Update the action mapping
            await jerkins_instance.async_update_action_mapping(area_id, actions)
        else:
            _LOGGER.error("Config entry %s not found", entry_id)
    
    # Register the services
    hass.services.async_register(
        DOMAIN, "force_update", force_update_service, schema=FORCE_UPDATE_SCHEMA
    )
    
    hass.services.async_register(
        DOMAIN, "update_area_mappings", update_area_mappings_service, schema=UPDATE_AREA_MAPPINGS_SCHEMA
    )
    
    hass.services.async_register(
        DOMAIN, "update_action_mappings", update_action_mappings_service, schema=UPDATE_ACTION_MAPPINGS_SCHEMA
    )
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    jerkins_ai = hass.data[DOMAIN].pop(entry.entry_id)
    await jerkins_ai.async_unload()
    
    # If this is the last instance, unregister the services
    if not hass.data[DOMAIN]:
        for service in ["force_update", "update_area_mappings", "update_action_mappings"]:
            hass.services.async_remove(DOMAIN, service)
    
    return True


class JerkinsAI:
    """Main class for Jerkins AI integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize the Jerkins AI."""
        self.hass = hass
        self.entry = entry
        self.llm_url = entry.data.get(CONF_URL)
        self.sensors = entry.data.get(CONF_SENSORS, [])
        self.area_mappings = entry.data.get(CONF_AREA_MAPPINGS, {})
        self.action_mappings = entry.data.get(CONF_ACTION_MAPPINGS, {})
        self.polling_interval = entry.data.get(
            CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
        )
        self._unsubscribe_polling = None
        self.session = None

    async def async_setup(self):
        """Set up the Jerkins AI integration."""
        self.session = async_get_clientsession(self.hass)
        
        # Start periodic polling
        self._unsubscribe_polling = async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(seconds=self.polling_interval),
        )
        
        # Do initial update
        await self.async_update()

        return True

    async def async_unload(self):
        """Unload the Jerkins AI integration."""
        if self._unsubscribe_polling:
            self._unsubscribe_polling()
            self._unsubscribe_polling = None
            
        return True

    async def async_update(self, *_):
        """Update sensor data and send to LLM for decision making."""
        try:
            # Collect sensor data with area information
            sensor_data = await self._collect_sensor_data()
            
            if not sensor_data:
                _LOGGER.warning("No sensor data collected, skipping LLM communication")
                return
                
            # Send data to LLM
            llm_response = await self._communicate_with_llm(sensor_data)
            
            if not llm_response:
                _LOGGER.warning("No response from LLM, no actions taken")
                return
                
            # Process LLM response and execute actions
            await self._process_llm_response(llm_response)
            
        except Exception as exc:
            _LOGGER.error("Error during update: %s", exc)
    
    async def async_update_area_mapping(self, sensor_id, area_id):
        """Update the area mapping for a sensor."""
        if sensor_id not in self.sensors:
            _LOGGER.error("Sensor %s is not configured in this integration", sensor_id)
            return False
        
        # Update area mapping
        self.area_mappings[sensor_id] = area_id
        
        # Save changes to config entry
        new_data = {**self.entry.data}
        new_data[CONF_AREA_MAPPINGS] = self.area_mappings
        
        self.hass.config_entries.async_update_entry(
            self.entry, data=new_data
        )
        
        _LOGGER.info("Updated area mapping for sensor %s to area %s", sensor_id, area_id)
        return True
    
    async def async_update_action_mapping(self, area_id, actions):
        """Update the available actions for an area."""
        # Update action mapping
        self.action_mappings[area_id] = actions
        
        # Save changes to config entry
        new_data = {**self.entry.data}
        new_data[CONF_ACTION_MAPPINGS] = self.action_mappings
        
        self.hass.config_entries.async_update_entry(
            self.entry, data=new_data
        )
        
        _LOGGER.info("Updated action mapping for area %s: %s", area_id, actions)
        return True
    
    async def _collect_sensor_data(self):
        """Collect data from all configured sensors."""
        sensor_data = []
        
        for sensor_entity_id in self.sensors:
            try:
                state = self.hass.states.get(sensor_entity_id)
                
                if not state:
                    _LOGGER.warning("Sensor %s not found", sensor_entity_id)
                    continue
                
                # Format the state value appropriately for binary sensors vs regular sensors
                state_value = state.state
                sensor_type = "sensor"
                
                # Special handling for binary sensors
                if sensor_entity_id.startswith("binary_sensor."):
                    sensor_type = "binary_sensor"
                    # Convert on/off, true/false to boolean for clearer understanding by LLM
                    if state_value.lower() in ['on', 'true', 'yes', 'open', 'detected', 'home']:
                        state_value = True
                    else:
                        state_value = False
                
                # Get available actions
                available_actions = self.action_mappings.get("default", [])
                valid_actions = await self._validate_actions(available_actions)
                
                sensor_info = {
                    "entity_id": sensor_entity_id,
                    "name": state.name,
                    "state": state_value,
                    "attributes": state.attributes,
                    "type": sensor_type,
                    "available_actions": valid_actions
                }
                
                sensor_data.append(sensor_info)
                
            except Exception as exc:
                _LOGGER.error("Error collecting data for sensor %s: %s", sensor_entity_id, exc)
        
        return sensor_data
    
    async def _validate_actions(self, configured_actions):
        """Validate that configured actions are still valid."""
        valid_actions = []
        
        for action in configured_actions:
            # Handle service calls (contains a dot)
            if "." in action:
                domain, service = action.split(".", 1)
                
                # Check if this domain is in our supported domains
                if domain not in SUPPORTED_DOMAINS:
                    _LOGGER.debug("Domain %s not in supported domains", domain)
                    continue
                
                # All service calls are considered valid
                valid_actions.append(action)
            else:
                # Custom actions are always considered valid
                valid_actions.append(action)
        
        return valid_actions
    
    async def _communicate_with_llm(self, sensor_data):
        """Send sensor data to the LLM and get decisions."""
        if not self.llm_url:
            _LOGGER.error("LLM URL not configured")
            return None
            
        try:
            # Make sure the URL format is correct for Ollama
            llm_url = self.llm_url
            if not llm_url.startswith("http://") and not llm_url.startswith("https://"):
                llm_url = f"http://{llm_url}"
                
            # Ensure URL ends with /api/generate for Ollama
            if not llm_url.endswith("/api/generate"):
                if llm_url.endswith("/"):
                    llm_url = f"{llm_url}api/generate"
                else:
                    llm_url = f"{llm_url}/api/generate"
            
            _LOGGER.debug("Using LLM URL: %s", llm_url)
            
            # Format the payload for Ollama's API
            prompt = (
                "You are the house brain. Analyze the following sensor data:\n\n"
                f"{sensor_data}\n\n"
                "If you think an action should be taken based on this data, respond with a "
                "JSON object with the format: {\"action\": \"action_name\", \"parameters\": {}}.\n"
                "If no action is needed, respond with an empty JSON object: {}."
            )
            
            payload = {
                "model": "jerkins",  # Use default model, can be configured later
                "prompt": prompt,
                "stream": False,
                "format": "json"
            }
            
            _LOGGER.debug("Sending request to LLM: %s", payload)
            
            async with async_timeout.timeout(30):
                async with self.session.post(llm_url, json=payload) as response:
                    if response.status != 200:
                        _LOGGER.error("LLM request failed with status %s: %s", 
                                     response.status, await response.text())
                        return None
                    
                    # Parse Ollama response format
                    response_data = await response.json()
                    _LOGGER.debug("LLM response: %s", response_data)
                    
                    # Extract the actual response from Ollama's response structure
                    # Ollama returns {"model": "...", "response": "...", ...}
                    if "response" in response_data:
                        try:
                            import json
                            # Try to parse the response as JSON
                            return json.loads(response_data["response"])
                        except json.JSONDecodeError:
                            _LOGGER.error("Failed to parse LLM response as JSON: %s", 
                                         response_data["response"])
                            return None
                    else:
                        _LOGGER.error("Unexpected response format from LLM: %s", response_data)
                        return None
                    
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout communicating with LLM")
            return None
        except aiohttp.ClientError as exc:
            _LOGGER.error("Error communicating with LLM: %s", exc)
            return None
        except Exception as exc:
            _LOGGER.error("Unexpected error in LLM communication: %s", exc)
            import traceback
            _LOGGER.debug("Traceback: %s", traceback.format_exc())
            return None
    
    async def _process_llm_response(self, llm_response):
        """Process LLM response and execute actions."""
        if not llm_response or not isinstance(llm_response, dict):
            _LOGGER.warning("Invalid LLM response format")
            return
            
        # Check if the response is empty (no actions needed)
        if not llm_response:
            _LOGGER.info("LLM determined no actions needed")
            return
            
        try:
            # The expected format is: {"action": "action_name", "parameters": {}}
            action = llm_response.get("action")
            parameters = llm_response.get("parameters", {})
            
            if not action:
                _LOGGER.warning("LLM response missing action: %s", llm_response)
                return
                
            # Get current valid actions
            configured_actions = self.action_mappings.get("default", [])
            valid_actions = await self._validate_actions(configured_actions)
            
            # Validate the action is available
            if action not in valid_actions:
                _LOGGER.warning(
                    "LLM requested action '%s' not available. Available actions: %s",
                    action, valid_actions
                )
                return
                
            # Execute the action
            await self._execute_action(action, parameters)
            
        except Exception as exc:
            _LOGGER.error("Error processing LLM response: %s", exc)
    
    async def _execute_action(self, action, parameters):
        """Execute an action in Home Assistant."""
        try:
            _LOGGER.info("Executing action '%s' with parameters: %s", 
                         action, parameters)
            
            # Check if action is a service call (contains a dot)
            if "." in action:
                domain, service = action.split(".", 1)
                
                # Create service data with parameters
                service_data = {**parameters}
                
                # Call the service
                await self.hass.services.async_call(
                    domain,
                    service,
                    service_data=service_data,
                )
                
                _LOGGER.info("Called service %s.%s with data %s", 
                            domain, service, service_data)
            else:
                # Handle custom actions - could be implemented based on specific needs
                _LOGGER.info("Custom action '%s' execution not yet implemented", action)
            
        except Exception as exc:
            _LOGGER.error("Error executing action: %s", exc)
``` 