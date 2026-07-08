import json
import uuid

import requests
from django.conf import settings

from config.utils.exceptions import AppException


class XuiApiException(AppException):
    default_message = "3x-ui panel error"


class ThreeXUiClient:
    """
    Thin wrapper around the 3x-ui (MHSanaei) panel REST API.

    IMPORTANT: the base path for these endpoints has changed between panel
    versions - some builds expose them under /panel/api/inbounds/...,
    others under /panel/inbound/.... Check your installed panel version
    (or /panel/api-docs if your build has it) and set XUI_API_BASE_PATH in
    settings/.env accordingly before relying on this in production.
    """

    def __init__(self, base_url=None, username=None, password=None, api_base_path=None):
        self.base_url = (base_url or settings.XUI_PANEL_URL).rstrip("/")
        self.username = username or settings.XUI_USERNAME
        self.password = password or settings.XUI_PASSWORD
        self.api_base_path = api_base_path or getattr(settings, "XUI_API_BASE_PATH", "/panel/api/inbounds")
        self.session = requests.Session()
        self._logged_in = False

    def _url(self, path):
        return f"{self.base_url}{path}"

    def login(self):
        response = self.session.post(
            self._url("/login"),
            data={"username": self.username, "password": self.password},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise XuiApiException(f"3x-ui login failed: {payload.get('msg')}")
        self._logged_in = True

    def _ensure_login(self):
        if not self._logged_in:
            self.login()

    def _request(self, method, path, **kwargs):
        self._ensure_login()
        response = self.session.request(method, self._url(path), timeout=15, **kwargs)
        if response.status_code == 401:
            # session cookie expired - re-login once and retry
            self.login()
            response = self.session.request(method, self._url(path), timeout=15, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", True):
            raise XuiApiException(payload.get("msg", "Unknown 3x-ui error"))
        return payload

    def add_client(self, inbound_id, email, total_gb, expiry_time_ms, limit_ip=0, flow=""):
        client_uuid = str(uuid.uuid4())
        settings_payload = {
            "clients": [
                {
                    "id": client_uuid,
                    "email": email,
                    "limitIp": limit_ip,
                    "totalGB": total_gb,
                    "expiryTime": expiry_time_ms,
                    "enable": True,
                    "flow": flow,
                }
            ]
        }
        self._request(
            "POST",
            f"{self.api_base_path}/addClient",
            json={"id": inbound_id, "settings": json.dumps(settings_payload)},
        )
        return client_uuid

    def update_client(self, client_uuid, inbound_id, email, total_gb, expiry_time_ms, limit_ip=0, enable=True):
        settings_payload = {
            "clients": [
                {
                    "id": client_uuid,
                    "email": email,
                    "limitIp": limit_ip,
                    "totalGB": total_gb,
                    "expiryTime": expiry_time_ms,
                    "enable": enable,
                }
            ]
        }
        return self._request(
            "POST",
            f"{self.api_base_path}/updateClient/{client_uuid}",
            json={"id": inbound_id, "settings": json.dumps(settings_payload)},
        )

    def delete_client(self, inbound_id, client_uuid):
        return self._request("POST", f"{self.api_base_path}/{inbound_id}/delClient/{client_uuid}")

    def get_client_traffic(self, email):
        payload = self._request("GET", f"{self.api_base_path}/getClientTraffics/{email}")
        return payload.get("obj")
