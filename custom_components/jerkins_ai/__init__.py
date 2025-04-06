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
from homeassistant.const import (
    CONF_URL,
)
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    CONF_SENSORS,
    CONF_ZONE_MAPPINGS,
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

UPDATE_ZONE_MAPPINGS_SCHEMA = vol.Schema({
    vol.Required("entry_id"): cv.string,
    vol.Required("sensor_id"): cv.entity_id,
    vol.Required("zone_id"): cv.string,
})

UPDATE_ACTION_MAPPINGS_SCHEMA = vol.Schema({
    vol.Required("entry_id"): cv.string,
    vol.Required("zone_id"): cv.string,
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
    
    async def update_zone_mappings_service(call: ServiceCall) -> None:
        """Handle the service call to update zone mappings."""
        entry_id = call.data.get("entry_id")
        sensor_id = call.data.get("sensor_id")
        zone_id = call.data.get("zone_id")
        
        if entry_id in hass.data[DOMAIN]:
            jerkins_instance = hass.data[DOMAIN][entry_id]
            # Update the zone mapping
            await jerkins_instance.async_update_zone_mapping(sensor_id, zone_id)
        else:
            _LOGGER.error("Config entry %s not found", entry_id)
    
    async def update_action_mappings_service(call: ServiceCall) -> None:
        """Handle the service call to update action mappings."""
        entry_id = call.data.get("entry_id")
        zone_id = call.data.get("zone_id")
        actions_str = call.data.get("actions")
        
        # Parse actions string into a list
        actions = [action.strip() for action in actions_str.split(",") if action.strip()]
        
        if entry_id in hass.data[DOMAIN]:
            jerkins_instance = hass.data[DOMAIN][entry_id]
            # Update the action mapping
            await jerkins_instance.async_update_action_mapping(zone_id, actions)
        else:
            _LOGGER.error("Config entry %s not found", entry_id)
    
    # Register the services
    hass.services.async_register(
        DOMAIN, "force_update", force_update_service, schema=FORCE_UPDATE_SCHEMA
    )
    
    hass.services.async_register(
        DOMAIN, "update_zone_mappings", update_zone_mappings_service, schema=UPDATE_ZONE_MAPPINGS_SCHEMA
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
        for service in ["force_update", "update_zone_mappings", "update_action_mappings"]:
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
        self.zone_mappings = entry.data.get(CONF_ZONE_MAPPINGS, {})
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
            # Collect sensor data with zone information
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
    
    async def async_update_zone_mapping(self, sensor_id, zone_id):
        """Update the zone mapping for a sensor."""
        if sensor_id not in self.sensors:
            _LOGGER.error("Sensor %s is not configured in this integration", sensor_id)
            return False
        
        # Update zone mapping
        self.zone_mappings[sensor_id] = zone_id
        
        # Save changes to config entry
        new_data = {**self.entry.data}
        new_data[CONF_ZONE_MAPPINGS] = self.zone_mappings
        
        self.hass.config_entries.async_update_entry(
            self.entry, data=new_data
        )
        
        _LOGGER.info("Updated zone mapping for sensor %s to zone %s", sensor_id, zone_id)
        return True
    
    async def async_update_action_mapping(self, zone_id, actions):
        """Update the available actions for a zone."""
        # Update action mapping
        self.action_mappings[zone_id] = actions
        
        # Save changes to config entry
        new_data = {**self.entry.data}
        new_data[CONF_ACTION_MAPPINGS] = self.action_mappings
        
        self.hass.config_entries.async_update_entry(
            self.entry, data=new_data
        )
        
        _LOGGER.info("Updated action mapping for zone %s: %s", zone_id, actions)
        return True
    
    async def _collect_sensor_data(self):
        """Collect data from all configured sensors and enrich with zone information."""
        sensor_data = []
        
        for sensor_entity_id in self.sensors:
            try:
                state = self.hass.states.get(sensor_entity_id)
                
                if not state:
                    _LOGGER.warning("Sensor %s not found", sensor_entity_id)
                    continue
                
                # Get the zone for this sensor
                zone_id = self.zone_mappings.get(sensor_entity_id)
                if not zone_id:
                    _LOGGER.warning("No zone mapping for sensor %s", sensor_entity_id)
                    continue
                
                # Get available actions for this zone
                available_actions = self.action_mappings.get(zone_id, [])
                
                # Dynamically check if these actions are still valid
                valid_actions = await self._validate_zone_actions(zone_id, available_actions)
                
                sensor_info = {
                    "entity_id": sensor_entity_id,
                    "name": state.name,
                    "state": state.state,
                    "attributes": state.attributes,
                    "zone": zone_id,
                    "available_actions": valid_actions
                }
                
                sensor_data.append(sensor_info)
                
            except Exception as exc:
                _LOGGER.error("Error collecting data for sensor %s: %s", sensor_entity_id, exc)
        
        return sensor_data
    
    async def _validate_zone_actions(self, zone_id, configured_actions):
        """Validate that configured actions for a zone are still valid."""
        valid_actions = []
        zone_entities = await self._get_entities_for_zone(zone_id)
        
        for action in configured_actions:
            # Handle service calls (contains a dot)
            if "." in action:
                domain, service = action.split(".", 1)
                
                # Check if this domain is in our supported domains
                if domain not in SUPPORTED_DOMAINS:
                    _LOGGER.debug("Domain %s not in supported domains", domain)
                    continue
                
                # Check if any entities of this domain exist in the zone
                domain_entities = [e for e in zone_entities if e.startswith(f"{domain}.")]
                
                if domain_entities or domain in ["script", "automation"]:
                    valid_actions.append(action)
                else:
                    _LOGGER.debug("No %s entities found in zone %s for action %s", 
                                 domain, zone_id, action)
            else:
                # Custom actions are always considered valid
                valid_actions.append(action)
        
        return valid_actions
    
    async def _get_entities_for_zone(self, zone_id):
        """Get entities that belong to a specific zone."""
        zone_entities = []
        
        # Check if this is a standard zone entity
        if zone_id.startswith("zone."):
            # This would typically use the zone's GPS location to find entities
            # For simplicity, we'll use a naming convention approach
            zone_name = zone_id.split(".", 1)[1]
        else:
            # For custom zones, use the zone ID directly
            zone_name = zone_id
        
        # Look for entities that might be in this zone based on entity_id or name
        states = self.hass.states.async_all()
        for state in states:
            # Only include supported domains
            if state.domain not in SUPPORTED_DOMAINS:
                continue
                
            # Check if entity might be in this zone
            entity_name = state.name.lower() if state.name else ""
            entity_id = state.entity_id.lower()
            
            # Match if zone name appears in entity ID or friendly name
            zone_key = zone_name.lower().replace("_", "")
            if (zone_key in entity_id or 
                zone_name.lower().replace("_", " ") in entity_name):
                zone_entities.append(state.entity_id)
                
        return zone_entities
    
    async def _communicate_with_llm(self, sensor_data):
        """Send sensor data to the LLM and get decisions."""
        if not self.llm_url:
            _LOGGER.error("LLM URL not configured")
            return None
            
        try:
            payload = {
                "system_prompt": "You are the house brain. Analyze the sensor data from different zones and decide if any actions should be taken.",
                "sensor_data": sensor_data,
                "instructions": "For each zone, decide if an action should be taken based on the sensor data. If yes, respond with a JSON object containing zone and action. If no action is needed, respond with an empty object."
            }
            
            async with async_timeout.timeout(30):
                async with self.session.post(self.llm_url, json=payload) as response:
                    if response.status != 200:
                        _LOGGER.error("LLM request failed with status %s", response.status)
                        return None
                        
                    return await response.json()
                    
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout communicating with LLM")
            return None
        except aiohttp.ClientError as exc:
            _LOGGER.error("Error communicating with LLM: %s", exc)
            return None
        except Exception as exc:
            _LOGGER.error("Unexpected error in LLM communication: %s", exc)
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
            # The expected format is: {"zone": "zone_id", "action": "action_name", "parameters": {}}
            zone = llm_response.get("zone")
            action = llm_response.get("action")
            parameters = llm_response.get("parameters", {})
            
            if not zone or not action:
                _LOGGER.warning("LLM response missing zone or action: %s", llm_response)
                return
                
            # Get current valid actions for this zone
            configured_actions = self.action_mappings.get(zone, [])
            valid_actions = await self._validate_zone_actions(zone, configured_actions)
            
            # Validate the action is available for this zone
            if action not in valid_actions:
                _LOGGER.warning(
                    "LLM requested action '%s' not available in zone '%s'. Available actions: %s",
                    action, zone, valid_actions
                )
                return
                
            # Execute the action
            await self._execute_action(zone, action, parameters)
            
        except Exception as exc:
            _LOGGER.error("Error processing LLM response: %s", exc)
    
    async def _execute_action(self, zone, action, parameters):
        """Execute an action in Home Assistant."""
        try:
            # Get entities related to this zone for context
            zone_entities = await self._get_entities_for_zone(zone)
            
            _LOGGER.info("Executing action '%s' in zone '%s' with parameters: %s", 
                         action, zone, parameters)
            
            # Check if action is a service call (contains a dot)
            if "." in action:
                domain, service = action.split(".", 1)
                
                # Create service data with parameters
                service_data = {**parameters}
                
                # Set target entity_id if not provided in parameters
                target = {}
                if "entity_id" in parameters:
                    target["entity_id"] = parameters.pop("entity_id")
                elif zone_entities:
                    # If entity_id not specified but we know entities in this zone,
                    # filter by the correct domain
                    matching_entities = [e for e in zone_entities if e.startswith(f"{domain}.")]
                    if matching_entities:
                        target["entity_id"] = matching_entities
                
                # Call the service
                await self.hass.services.async_call(
                    domain,
                    service,
                    service_data=service_data,
                    target=target,
                )
                
                _LOGGER.info("Called service %s.%s with data %s and target %s", 
                            domain, service, service_data, target)
            else:
                # Handle custom actions - could be implemented based on specific needs
                _LOGGER.info("Custom action '%s' execution not yet implemented", action)
            
        except Exception as exc:
            _LOGGER.error("Error executing action: %s", exc)