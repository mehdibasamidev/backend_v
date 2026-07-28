import requests
from django.conf import settings

from config.utils.exceptions import AppException


class XuiApiException(AppException):
    default_message = "3x-ui panel error"


class ThreeXUiClient:
    """
    Thin wrapper around the 3x-ui (MHSanaei) panel REST API v3.5+.

    Auth is a static Bearer API token (Settings -> Security -> API Token
    on the panel) - no login/session/cookie handling needed, every
    request just carries the Authorization header.
    """

    def __init__(self, base_url=None, api_token=None):
        # Must include any custom path prefix the panel is mounted under,
        # e.g. "https://epanel.example.com:2087/bmehdib" (NOT just the host).
        self.base_url = (base_url or settings.XUI_PANEL_BASE_URL).rstrip("/")
        self.api_token = api_token or settings.XUI_API_TOKEN
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        })

    def _url(self, path):
        return f"{self.base_url}{path}"

    def _request(self, method, path, **kwargs):
        response = self.session.request(method, self._url(path), timeout=15, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", True):
            raise XuiApiException(payload.get("msg") or "Unknown 3x-ui error")
        return payload

    def add_client(self, email, total_gb, expiry_time_ms, inbound_ids, limit_ip=0, tg_id=0):
        """
        Creates the client and attaches it to every inbound in inbound_ids
        in one call. uuid/subId are generated server-side when omitted -
        fetch them afterwards with get_client().
        """
        payload = {
            "client": {
                "email": email,
                "totalGB": total_gb,
                "expiryTime": expiry_time_ms,
                "tgId": tg_id,
                "limitIp": limit_ip,
                "enable": True,
            },
            "inboundIds": inbound_ids,
        }
        return self._request("POST", "/panel/api/clients/add", json=payload)

    def get_client(self, email):
        """Returns {client, externalLinks, inboundIds, usedTraffic}."""
        payload = self._request("GET", f"/panel/api/clients/get/{email}")
        return payload.get("obj") or {}

    def bulk_adjust(self, emails, add_days=0, add_bytes=0, flow=None):
        """
        Shifts expiry/quota for one or more clients (values may be
        negative). Clients with unlimited expiry (expiryTime=0) or
        unlimited traffic (totalGB=0) are skipped for that field by the
        panel itself - matches our own volume_gb/duration "0 = unlimited"
        convention.
        """
        payload = {"emails": emails, "addDays": add_days, "addBytes": add_bytes}
        if flow is not None:
            payload["flow"] = flow
        return self._request("POST", "/panel/api/clients/bulkAdjust", json=payload)

    def get_traffic(self, email):
        payload = self._request("GET", f"/panel/api/clients/traffic/{email}")
        return payload.get("obj") or {}

    def get_links(self, email):
        """List of per-location config strings (vless://, vmess://, ...)."""
        payload = self._request("GET", f"/panel/api/clients/links/{email}")
        return payload.get("obj") or []

    def delete_client(self, email):
        return self._request("POST", f"/panel/api/clients/del/{email}")

    def bulk_enable(self, emails):
        return self._request("POST", "/panel/api/clients/bulkEnable", json={"emails": emails})

    def bulk_disable(self, emails):
        return self._request("POST", "/panel/api/clients/bulkDisable", json={"emails": emails})
