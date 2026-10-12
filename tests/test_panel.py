import httpx
import pytest

from core.panel import PanelError, RealPanel, canonical


def test_record_id_is_not_vless_uuid():
    result = canonical(
        {
            "client": {"id": 17, "uuid": "vless-uuid", "email": "alice", "expiryTime": 0},
            "inboundIds": [5, 3],
            "usedTraffic": 12,
        }
    )
    assert result["record_id"] == 17
    assert result["client"]["id"] == "vless-uuid"
    assert result["inboundIds"] == [3, 5]


def test_adapter_authenticated_update_preserves_all_fields(settings):
    settings.PANEL_URL = "https://panel.test/private/"
    settings.PANEL_TOKEN = "synthetic-api-token"
    settings.PANEL_WRITES_ENABLED = True
    seen = []

    def handler(request):
        import json

        seen.append(request)
        if "/update/" in request.url.path:
            body = json.loads(request.content)
            assert body["id"] == "vless-uuid" and body["password"] == "test-secret"
            assert body["unknownFutureField"] == "keep"
            return httpx.Response(200, json={"success": True})
        if request.url.path.endswith("/list"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "obj": [{"id": 17, "uuid": "vless-uuid", "email": "alice", "inboundIds": [3]}],
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "obj": {
                    "client": {
                        "id": 17,
                        "uuid": "vless-uuid",
                        "email": "alice",
                        "password": "test-secret",
                        "unknownFutureField": "keep",
                    },
                    "inboundIds": [3],
                },
            },
        )

    adapter = RealPanel()
    adapter.client.close()
    adapter.client = httpx.Client(
        base_url=settings.PANEL_URL,
        headers={"Authorization": "Bearer synthetic-api-token"},
        transport=httpx.MockTransport(handler),
    )
    state = adapter.get("alice")
    state["client"]["expiryTime"] = 123456
    adapter.update("alice", state)
    assert all(request.headers["Authorization"] == "Bearer synthetic-api-token" for request in seen)
    assert seen[-1].url.path == "/private/panel/api/clients/update/alice"
    adapter.client.close()


def test_writes_disabled_by_default(settings):
    settings.PANEL_URL = "https://panel.test/"
    settings.PANEL_TOKEN = "synthetic-token"
    adapter = RealPanel()
    with pytest.raises(PanelError, match="выключена"):
        adapter.update("alice", {"client": {}})
    adapter.client.close()


def test_node_pending_not_claimed_success(settings):
    settings.PANEL_URL = "https://panel.test/"
    settings.PANEL_TOKEN = "synthetic-token"
    settings.PANEL_WRITES_ENABLED = True
    adapter = RealPanel()
    adapter.client.close()
    adapter.client = httpx.Client(
        base_url=settings.PANEL_URL,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"success": True, "obj": {"nodePending": True}})
        ),
    )
    with pytest.raises(PanelError):
        adapter.update("alice", {"client": {}})
    adapter.client.close()


def test_empty_legacy_reverse_is_json_null():
    result = canonical(
        {
            "client": {"email": "alice", "id": 17, "uuid": "test", "reverse": "", "allowedIPs": ""},
            "inboundIds": [3],
        }
    )
    assert result["client"]["reverse"] is None
    assert result["client"]["allowedIPs"] == []


def test_node_and_each_inbound_must_match():
    import copy
    import time

    adapter = object.__new__(RealPanel)
    c = {"email": "alice", "id": "test-uuid", "expiryTime": 9999999999999, "totalGB": 0, "enable": True}
    state = {"client": c, "inboundIds": [3, 10]}
    inbounds = [
        {"id": 3, "enable": True, "protocol": "vless", "settings": {"clients": [copy.deepcopy(c)]}},
        {
            "id": 10,
            "enable": True,
            "protocol": "vless",
            "nodeId": 1,
            "settings": {"clients": [copy.deepcopy(c)]},
        },
    ]
    nodes = [{"id": 1, "status": "online", "xrayState": "running", "lastHeartbeat": time.time()}]
    adapter.request = lambda method, path: (
        {"xray": {"state": "running"}}
        if path.endswith("/status")
        else inbounds
        if path.endswith("inbounds/list")
        else nodes
    )
    assert adapter.healthy(state)
    inbounds[1]["settings"]["clients"][0]["expiryTime"] -= 10000
    assert not adapter.healthy(state)
    inbounds[1]["settings"]["clients"][0] = copy.deepcopy(c)
    nodes[0]["configDirty"] = True
    assert not adapter.healthy(state)
