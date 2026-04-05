"""Data update coordinator for GL.iNet integration."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GLiNetAPI
from .const import CONF_HOST, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class GLiNetDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the GL.iNet router."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.api = GLiNetAPI(
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD]
        )
        self.wifi_capable: bool = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def async_detect_capabilities(self) -> None:
        """Probe hardware capabilities once at setup time.

        On wired-only devices (e.g. MT2500/Brume 2) the wifi RPC service does
        not exist and every call returns None.  We detect this once here so the
        main poll loop can skip those calls entirely rather than issuing two
        guaranteed-to-fail requests every cycle.
        """
        result = await self.hass.async_add_executor_job(self.api.get_wifi_config)
        self.wifi_capable = result is not None
        _LOGGER.debug(
            "GL.iNet capability detection: wifi_capable=%s", self.wifi_capable
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from all API endpoints concurrently."""
        try:
            # --- core calls (always issued concurrently) ---
            core_results = await asyncio.gather(
                self.hass.async_add_executor_job(self.api.get_active_vpn),
                self.hass.async_add_executor_job(self.api.get_system_status),
                self.hass.async_add_executor_job(self.api.get_system_info),
                self.hass.async_add_executor_job(self.api.get_disk_info),
                self.hass.async_add_executor_job(self.api.get_all_vpn_configs),
                self.hass.async_add_executor_job(self.api.get_load),
                self.hass.async_add_executor_job(self.api.get_timezone_config),
                self.hass.async_add_executor_job(self.api.get_security_policy),
                self.hass.async_add_executor_job(self.api.get_firewall_rules),
                self.hass.async_add_executor_job(self.api.get_dmz_config),
                self.hass.async_add_executor_job(self.api.get_port_forward_list),
                self.hass.async_add_executor_job(self.api.get_wan_access),
                self.hass.async_add_executor_job(self.api.get_zone_list),
                self.hass.async_add_executor_job(self.api.get_wg_server_status),
                self.hass.async_add_executor_job(self.api.get_wg_server_config),
                self.hass.async_add_executor_job(self.api.get_ovpn_server_status),
                self.hass.async_add_executor_job(self.api.get_clients),
            )
            (
                vpn_status,
                system_status,
                system_info,
                disk_info,
                vpn_configs,
                load_info,
                timezone_config,
                security_policy,
                firewall_rules,
                dmz_config,
                port_forwards,
                wan_access,
                zone_list,
                wg_server_status,
                wg_server_config,
                ovpn_server_status,
                clients,
            ) = core_results

            # --- WiFi calls (skipped entirely on wired-only hardware) ---
            if self.wifi_capable:
                wifi_config, wifi_status_detail = await asyncio.gather(
                    self.hass.async_add_executor_job(self.api.get_wifi_config),
                    self.hass.async_add_executor_job(self.api.get_wifi_status),
                )
            else:
                wifi_config = None
                wifi_status_detail = None

            return {
                "vpn_status": vpn_status,
                "system_status": system_status,
                "system_info": system_info,
                "disk_info": disk_info,
                "vpn_configs": vpn_configs,
                "load_info": load_info,
                "timezone_config": timezone_config,
                "security_policy": security_policy,
                "firewall_rules": firewall_rules,
                "dmz": dmz_config,
                "port_forwards": port_forwards,
                "wan_access": wan_access,
                "zone_list": zone_list,
                "wg_server_status": wg_server_status,
                "wg_server_config": wg_server_config,
                "ovpn_server_status": ovpn_server_status,
                "wifi_config": wifi_config,
                "wifi_status_detail": wifi_status_detail,
                "clients": clients,
            }

        except Exception as exc:
            raise UpdateFailed(f"Error communicating with API: {exc}") from exc

    async def async_start_vpn(self, vpn_name: str) -> bool:
        """Start a VPN connection."""
        vpn_configs = self.data.get("vpn_configs", [])

        for config in vpn_configs:
            if config.get("name") == vpn_name:
                await self.hass.async_add_executor_job(self.api.stop_all_vpns)
                result = await self.hass.async_add_executor_job(self.api.start_vpn, config)
                if result:
                    await self.async_request_refresh()
                return result

        _LOGGER.error("VPN configuration not found: %s", vpn_name)
        return False

    async def async_stop_vpn(self, vpn_name: str) -> bool:
        """Stop a specific VPN connection."""
        vpn_configs = self.data.get("vpn_configs", [])

        for config in vpn_configs:
            if config.get("name") == vpn_name:
                result = await self.hass.async_add_executor_job(self.api.stop_vpn, config)
                if result:
                    await self.async_request_refresh()
                return result

        _LOGGER.error("VPN configuration not found: %s", vpn_name)
        return False

    async def async_stop_all_vpns(self) -> bool:
        """Stop all VPN connections."""
        result = await self.hass.async_add_executor_job(self.api.stop_all_vpns)
        if result:
            await self.async_request_refresh()
        return result

    async def async_reboot_system(self) -> bool:
        """Reboot the router."""
        return await self.hass.async_add_executor_job(self.api.reboot_system)

    async def async_check_firmware(self) -> Dict[str, Any]:
        """Check for firmware updates."""
        return await self.hass.async_add_executor_job(self.api.check_firmware_online)

    async def async_start_wg_server(self) -> bool:
        """Start WireGuard server."""
        result = await self.hass.async_add_executor_job(self.api.start_wg_server)
        if result and not result.get("err_code"):
            await self.async_request_refresh()
            return True
        return False

    async def async_stop_wg_server(self) -> bool:
        """Stop WireGuard server."""
        result = await self.hass.async_add_executor_job(self.api.stop_wg_server)
        if result and not result.get("err_code"):
            await self.async_request_refresh()
            return True
        return False

    async def async_start_ovpn_server(self) -> bool:
        """Start OpenVPN server."""
        result = await self.hass.async_add_executor_job(self.api.start_ovpn_server)
        if result and not result.get("err_code"):
            await self.async_request_refresh()
            return True
        return False

    async def async_stop_ovpn_server(self) -> bool:
        """Stop OpenVPN server."""
        result = await self.hass.async_add_executor_job(self.api.stop_ovpn_server)
        if result and not result.get("err_code"):
            await self.async_request_refresh()
            return True
        return False

    async def async_set_wifi_enabled(self, iface_name: str, enabled: bool) -> bool:
        """Enable or disable a WiFi interface."""
        result = await self.hass.async_add_executor_job(
            self.api.set_wifi_config,
            {"iface_name": iface_name, "enabled": enabled}
        )
        if result and not result.get("err_code"):
            await self.async_request_refresh()
            return True
        return False


# Backward compatibility alias
GLiNetCoordinator = GLiNetDataUpdateCoordinator
