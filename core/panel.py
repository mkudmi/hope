"""3x-ui v3.9.0 adapter. Never touches node databases or infrastructure settings."""

import copy
import json
import secrets
import time
import uuid
from functools import lru_cache
from urllib.parse import quote, urlparse

import httpx
from django.conf import settings

from .crypto import seal, unseal
from .models import DemoClient


class PanelError(Exception):
    def __init__(self, message="Панель недоступна. Повторите проверку позже.", ambiguous=False):
        super().__init__(message)
        self.ambiguous = ambiguous


class PanelMissing(PanelError):
    pass


def canonical(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("client", payload), dict):
        raise PanelError("Неизвестная структура клиента.")
    raw = copy.deepcopy(payload.get("client", payload))
    record_id = raw.get("id") if "uuid" in raw else None
    if "uuid" in raw:
        raw["id"] = raw.pop("uuid")
    for key in ("createdAt", "updatedAt", "created_at", "updated_at", "inboundIds", "traffic"):
        raw.pop(key, None)
    for field in ("reverse", "allowedIPs"):
        if isinstance(raw.get(field), str) and raw[field]:
            try:
                raw[field] = json.loads(raw[field])
            except ValueError:
                raise PanelError("Неизвестный формат клиента. Нужна проверка контракта API.") from None
    if raw.get("allowedIPs") == "":
        raw["allowedIPs"] = []
    if raw.get("reverse") == "":
        raw["reverse"] = None
    traffic = payload.get("traffic") or {}
    return {
        "client": raw,
        "record_id": record_id,
        "inboundIds": sorted(payload.get("inboundIds") or []),
        "usedTraffic": payload.get("usedTraffic", traffic.get("up", 0) + traffic.get("down", 0)),
    }


def identity_fields(state):
    c = state["client"]
    return (state.get("record_id"), c.get("id"), c.get("password"), c.get("auth"), c.get("subId"))


def same_config(left, right):
    # Counters and timestamps are intentionally not compared.
    return left["client"] == right["client"] and left["inboundIds"] == right["inboundIds"]


class RealPanel:
    def __init__(self):
        parsed = urlparse(settings.PANEL_URL)
        if parsed.scheme != "https" or not parsed.hostname or not settings.PANEL_TOKEN:
            raise PanelError("Укажите HTTPS адрес и API-токен 3x-ui в .env.")
        self.client = httpx.Client(
            base_url=settings.PANEL_URL,
            headers={"Authorization": f"Bearer {settings.PANEL_TOKEN}"},
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
        )

    def request(self, method, path, body=None):
        if method != "GET" and not settings.PANEL_WRITES_ENABLED:
            raise PanelError("Запись в панель выключена в .env.")
        try:
            response = self.client.request(method, path, json=body)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise PanelError(ambiguous=method != "GET") from None
        if not isinstance(data, dict) or data.get("success") is not True:
            # Server messages can contain credentials: never propagate them to logs/UI.
            raise PanelError(
                "Панель отклонила запрос; требуется сверка результата.", ambiguous=method != "GET"
            )
        if (
            method != "GET"
            and isinstance(data.get("obj"), dict)
            and (data["obj"].get("nodePending") or data["obj"].get("nodeSyncPending"))
        ):
            raise PanelError("Панель ожидает применения на нодах.", ambiguous=True)
        return data.get("obj")

    def list(self):
        rows = self.request("GET", "panel/api/clients/list")
        if not isinstance(rows, list):
            raise PanelError("Неизвестный формат списка клиентов.")
        return [canonical(row) for row in rows]

    def get(self, email):
        # Distinguish absence from authentication/network failure, using the list contract.
        if not any(row["client"]["email"] == email for row in self.list()):
            raise PanelMissing("Клиент не найден в панели.")
        result = self.request("GET", "panel/api/clients/get/" + quote(email, safe=""))
        if not isinstance(result, dict) or not isinstance(result.get("client"), dict):
            raise PanelError("Неизвестный формат карточки клиента.")
        return canonical(result)

    def create(self, state):
        return self.request(
            "POST", "panel/api/clients/add", {"client": state["client"], "inboundIds": state["inboundIds"]}
        )

    def update(self, email, state):
        return self.request("POST", "panel/api/clients/update/" + quote(email, safe=""), state["client"])

    def healthy(self, state):
        server = self.request("GET", "panel/api/server/status")
        if not isinstance(server, dict) or server.get("xray", {}).get("state") != "running":
            return False
        inbounds = self.request("GET", "panel/api/inbounds/list")
        nodes = self.request("GET", "panel/api/nodes/list")
        if not isinstance(inbounds, list) or not isinstance(nodes, list):
            raise PanelError("Неизвестный формат состояния нод.")
        mapped = {x["id"]: x for x in nodes}
        selected = [x for x in inbounds if x["id"] in state["inboundIds"]]
        if len(selected) != len(state["inboundIds"]) or not selected:
            return False
        for inbound in selected:
            if not inbound.get("enable"):
                return False
            inbound_settings = inbound.get("settings") or {}
            if isinstance(inbound_settings, str):
                try:
                    inbound_settings = json.loads(inbound_settings)
                except ValueError:
                    return False
            if not isinstance(inbound_settings, dict):
                return False
            entry = next(
                (
                    c
                    for c in inbound_settings.get("clients", [])
                    if c.get("email") == state["client"]["email"]
                ),
                None,
            )
            if entry is None:
                if state["client"].get("enable"):
                    return False
            elif any(
                entry.get(field, 0) != state["client"].get(field, 0)
                for field in ("expiryTime", "totalGB", "enable")
            ):
                return False
            protocol = inbound.get("protocol")
            secret_field = (
                "auth"
                if protocol in ("hysteria", "hysteria2")
                else "password"
                if protocol in ("trojan", "shadowsocks")
                else "id"
            )
            if entry is not None and entry.get(secret_field) != state["client"].get(secret_field):
                return False
            node_id = inbound.get("nodeId")
            if node_id:
                node = mapped.get(node_id, {})
                if (
                    node.get("status") != "online"
                    or node.get("xrayState") != "running"
                    or node.get("configDirty")
                    or node.get("lastError")
                    or node.get("xrayError")
                    or time.time() - node.get("lastHeartbeat", 0) > 120
                ):
                    return False
        return True


class DemoPanel:
    def list(self):
        return [unseal(row.data) for row in DemoClient.objects.all()]

    def get(self, email):
        try:
            return unseal(DemoClient.objects.get(email=email).data)
        except DemoClient.DoesNotExist:
            raise PanelMissing("Клиент не найден в демонстрационной панели.") from None

    def create(self, state):
        email = state["client"]["email"]
        if DemoClient.objects.filter(email=email).exists():
            raise PanelError("Клиент уже существует.")
        stored = copy.deepcopy(state)
        stored["record_id"] = secrets.randbelow(10**9)
        DemoClient.objects.create(email=email, data=seal(stored))

    def update(self, email, state):
        old = self.get(email)
        stored = copy.deepcopy(state)
        stored["record_id"] = old["record_id"]
        stored["usedTraffic"] = old.get("usedTraffic", 0)
        DemoClient.objects.filter(email=email).update(data=seal(stored))

    def healthy(self, state):
        return bool(state["inboundIds"])


@lru_cache(maxsize=2)
def _cached_panel(mode, url, token):
    if mode == "demo":
        return DemoPanel()
    if mode == "real":
        return RealPanel()
    raise PanelError("PANEL_MODE должен быть demo или real.")


def panel():
    return _cached_panel(settings.PANEL_MODE, settings.PANEL_URL, settings.PANEL_TOKEN)


def new_state(email, expiry_ms, total_bytes=0, limit_devices=0):
    return {
        "record_id": None,
        "usedTraffic": 0,
        "inboundIds": sorted(settings.PANEL_INBOUND_IDS),
        "client": {
            "email": email,
            "id": str(uuid.uuid4()),
            "subId": secrets.token_hex(16),
            "password": secrets.token_urlsafe(24),
            "auth": secrets.token_urlsafe(24),
            "flow": "xtls-rprx-vision",
            "security": "auto",
            "enable": True,
            "tgId": 0,
            "limitIp": 0,
            "limitHwid": limit_devices,
            "totalGB": total_bytes,
            "expiryTime": expiry_ms,
            "reset": 0,
            "resetDay": 0,
            "resetWeekday": 0,
            "resetMax": 0,
            "trafficReset": "never",
            "trafficResetDay": 1,
            "comment": "HOPE",
        },
    }
