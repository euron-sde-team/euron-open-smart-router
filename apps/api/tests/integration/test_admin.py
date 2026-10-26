"""Dashboard API: authentication, roles, CSRF, guards, audit, and every resource's CRUD."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import OWNER, Api, World

pytestmark = pytest.mark.integration


async def login(api: Api, email: str, password: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as c:
        return await c.post("/api/v1/auth/login", json={"email": email, "password": password})


async def as_user(api: Api, role: str) -> httpx.AsyncClient:
    email = f"{role}@example.test"
    await api.ok(
        "POST",
        "/api/v1/users",
        {"email": email, "name": role.title(), "role": role, "password": "Role-Pass-1234"},
        status=201,
    )
    token = (await login(api, email, "Role-Pass-1234")).json()["session_token"]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://testserver",
        headers={"authorization": f"Bearer {token}"},
    )


async def test_login_me_logout(api: Api) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as c:
        resp = await c.post(
            "/api/v1/auth/login",
            json={"email": "OWNER@example.test", "password": OWNER["password"]},
        )
        assert resp.status_code == 200 and "sr_session" in resp.cookies
        assert (await c.get("/api/v1/auth/me")).json()["role"] == "owner"
        await c.post("/api/v1/auth/logout", headers={"x-smartrouter-csrf": "1"})
        c.cookies.clear()
        assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_cookie_mutations_need_csrf_header(api: Api) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as c:
        await c.post("/api/v1/auth/login", json=OWNER)
        assert (await c.post("/api/v1/teams", json={"name": "x"})).json()["error"][
            "code"
        ] == "csrf_required"
        assert (
            await c.post("/api/v1/teams", json={"name": "x"}, headers={"x-smartrouter-csrf": "1"})
        ).status_code == 201
        assert (await c.get("/api/v1/teams")).status_code == 200


async def test_wrong_password_lockout(api: Api) -> None:
    for _ in range(5):
        assert (await login(api, OWNER["email"], "wrong")).json()["error"][
            "code"
        ] == "invalid_credentials"
    locked = await login(api, OWNER["email"], OWNER["password"])
    assert locked.status_code == 401 and locked.json()["error"]["code"] == "account_locked"


async def test_unknown_user_gets_same_error(api: Api) -> None:
    assert (await login(api, "nobody@example.test", "x")).json()["error"][
        "code"
    ] == "invalid_credentials"


async def test_password_change_ends_other_sessions(api: Api) -> None:
    old = (await login(api, OWNER["email"], OWNER["password"])).json()["session_token"]
    resp = await api.post(
        "/api/v1/auth/password",
        {"current_password": OWNER["password"], "new_password": "Brand-New-Pass-9"},
    )
    assert resp.status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as c:
        assert (
            await c.get("/api/v1/auth/me", headers={"authorization": f"Bearer {old}"})
        ).status_code == 401
    weak = await api.post(
        "/api/v1/auth/password",
        {"current_password": "Brand-New-Pass-9", "new_password": "weakpassword"},
    )
    assert weak.status_code in (400, 401)


async def test_viewer_is_read_only(api: Api, world: World) -> None:
    viewer = await as_user(api, "viewer")
    async with viewer:
        for path in (
            "/api/v1/projects",
            "/api/v1/policies",
            "/api/v1/decisions",
            "/api/v1/analytics/overview",
            "/api/v1/models",
            "/api/v1/quality",
            "/api/v1/health/models",
            "/api/v1/benchmarks/runs",
        ):
            assert (await viewer.get(path)).status_code == 200, path
        assert (await viewer.post("/api/v1/teams", json={"name": "x"})).status_code == 403
        assert (await viewer.post(f"/api/v1/keys/{world.key_id}/revoke")).status_code == 403
        assert (await viewer.get("/api/v1/audit")).status_code == 403
        assert (await viewer.get("/api/v1/users")).status_code == 403
        assert (await viewer.post("/api/v1/policies", json={"name": "x"})).status_code == 403
        assert (await viewer.post("/api/v1/benchmarks/runs", json={})).status_code == 403
        sim = await viewer.post(
            "/api/v1/route/simulate", json={"messages": [{"role": "user", "content": "hi"}]}
        )
        assert sim.status_code == 200


async def test_admin_cannot_manage_users(api: Api) -> None:
    admin = await as_user(api, "admin")
    async with admin:
        assert (await admin.post("/api/v1/teams", json={"name": "ok"})).status_code == 201
        resp = await admin.post(
            "/api/v1/users",
            json={
                "email": "x@example.test",
                "name": "x",
                "role": "viewer",
                "password": "Some-Pass-123",
            },
        )
        assert resp.status_code == 403


async def test_owner_guards(api: Api) -> None:
    me = await api.ok("GET", "/api/v1/auth/me")
    assert (
        await api.client.patch(f"/api/v1/users/{me['id']}", json={"role": "viewer"})
    ).status_code == 403
    other = await api.ok(
        "POST",
        "/api/v1/users",
        {"email": "o2@example.test", "name": "O2", "role": "owner", "password": "Owner-Two-123"},
        status=201,
    )
    await api.ok("PATCH", f"/api/v1/users/{other['id']}", {"disabled": True})
    assert (await login(api, "o2@example.test", "Owner-Two-123")).json()["error"][
        "code"
    ] == "account_disabled"
    dup = await api.post(
        "/api/v1/users",
        {"email": "O2@example.test", "name": "x", "role": "viewer", "password": "Some-Pass-123"},
    )
    assert dup.status_code == 409


async def test_role_change_invalidates_sessions(api: Api) -> None:
    async with await as_user(api, "admin") as admin:
        users = await api.ok("GET", "/api/v1/users")
        target = next(u for u in users if u["role"] == "admin")
        await api.ok("PATCH", f"/api/v1/users/{target['id']}", {"role": "viewer"})
        assert (await admin.get("/api/v1/auth/me")).status_code == 401


async def test_resource_crud_and_audit(api: Api, world: World) -> None:
    team = await api.ok("POST", "/api/v1/teams", {"name": "Growth"}, status=201)
    await api.ok("PATCH", f"/api/v1/teams/{team['id']}", {"description": "copy"})
    project = await api.ok(
        "POST", "/api/v1/projects", {"name": "Growth bot", "team_id": team["id"]}, status=201
    )
    conflict = await api.client.delete(f"/api/v1/teams/{team['id']}")
    assert conflict.status_code == 409
    await api.ok("PATCH", f"/api/v1/projects/{project['id']}", {"archived": True})
    assert (await api.client.delete(f"/api/v1/teams/{team['id']}")).status_code == 204
    teams = await api.ok("GET", "/api/v1/teams")
    assert all(t["id"] != team["id"] for t in teams)
    assert (
        await api.post("/api/v1/projects", {"name": "x", "default_policy": "nope"})
    ).status_code == 400

    key = await api.ok(
        "POST", "/api/v1/keys", {"project_id": world.project_id, "name": "ci"}, status=201
    )
    assert key["key"].startswith("tsr_") and key["display"].endswith("_****")
    listed = await api.ok("GET", f"/api/v1/keys?project_id={world.project_id}")
    assert all("key" not in k for k in listed), "plaintext is shown once"
    await api.ok("PATCH", f"/api/v1/keys/{key['id']}", {"rpm_limit": 10})

    p = await api.ok(
        "POST",
        "/api/v1/policies",
        {"name": "cheap-first", "strategy": "cost", "quality_floor": 0.6},
        status=201,
    )
    assert p["effective_weights"]["cost"] == pytest.approx(0.7)
    await api.ok("PATCH", f"/api/v1/policies/{p['id']}", {"is_default": True})
    policies = await api.ok("GET", "/api/v1/policies")
    assert [x["name"] for x in policies if x["is_default"]] == ["cheap-first"]
    bad = await api.post("/api/v1/policies", {"name": "Bad Name"})
    assert bad.status_code == 422
    unknown = await api.post("/api/v1/policies", {"name": "x", "allowed_models": ["nope"]})
    assert unknown.status_code == 400
    floors = await api.post("/api/v1/policies", {"name": "y", "task_floors": {"poetry": 0.5}})
    assert floors.status_code == 422
    dup = await api.post("/api/v1/policies", {"name": "cheap-first"})
    assert dup.status_code == 409
    guarded = await api.client.delete(f"/api/v1/policies/{p['id']}")
    assert guarded.status_code == 409 and guarded.json()["error"]["code"] == "default_policy"
    await api.ok("PATCH", f"/api/v1/policies/{world.policies['balanced']}", {"is_default": True})
    assert (await api.client.delete(f"/api/v1/policies/{p['id']}")).status_code == 204

    audit = await api.ok("GET", "/api/v1/audit?limit=200")
    actions = {e["action"] for e in audit["items"]}
    assert {
        "team.created",
        "project.updated",
        "api_key.created",
        "policy.deleted",
        "policy.created",
        "auth.login",
    } <= actions
    assert all(e["actor_email"] for e in audit["items"])
    page = await api.ok("GET", "/api/v1/audit?limit=3")
    nxt = await api.ok("GET", f"/api/v1/audit?limit=3&cursor={page['next_cursor']}")
    assert page["items"][-1]["id"] > nxt["items"][0]["id"]


async def test_endpoints_and_models(api: Api, world: World) -> None:
    providers = await api.ok("GET", "/api/v1/providers")
    ep = next(p for p in providers if p["id"] == world.endpoints["openai"])
    assert ep["has_key"] and ep["key_hint"] == "...3456" and "api_key" not in ep
    assert ep["simulated"] and ep["model_count"] == 2
    check = await api.ok("POST", f"/api/v1/providers/{world.endpoints['openai']}/test")
    assert check["ok"] and check["message"] == "Connected"
    models = await api.ok("GET", "/api/v1/models")
    pro = next(m for m in models if m["alias"] == "gpt-pro")
    assert pro["tier"] == "advanced" and float(pro["output_per_m"]) == 12
    await api.ok("PATCH", f"/api/v1/models/{world.models['gpt-pro']}", {"output_per_m": "24"})
    simulated = await api.ok(
        "POST",
        "/api/v1/route/simulate",
        {"model": "auto:quality", "messages": [{"role": "user", "content": "Solve 3x + 7 = 22"}]},
    )
    pro_cand = next(c for c in simulated["candidates"] if c["model"] == "gpt-pro")
    assert pro_cand["estimated_cost_nusd"] > 0
    dup = await api.post(
        "/api/v1/models",
        {"alias": "gpt-pro", "provider_id": world.endpoints["openai"], "upstream_model": "x"},
    )
    assert dup.status_code == 409
    reserved = await api.post(
        "/api/v1/models",
        {"alias": "auto", "provider_id": world.endpoints["openai"], "upstream_model": "x"},
    )
    assert reserved.status_code in (400, 409, 422)
    await api.ok("PATCH", f"/api/v1/providers/{world.endpoints['openai']}", {"api_key": ""})
    providers = await api.ok("GET", "/api/v1/providers")
    assert not next(p for p in providers if p["id"] == world.endpoints["openai"])["has_key"]


async def test_disabling_a_model_removes_it_from_routing(api: Api, world: World) -> None:
    await api.ok("PATCH", f"/api/v1/models/{world.models['llama-local']}", {"enabled": False})
    r = await api.gateway(
        world.key,
        {
            "model": "auto:cost",
            "messages": [{"role": "user", "content": "Classify as spam or ham"}],
        },
    )
    assert r.headers["x-smartrouter-model"] != "llama-local"


async def test_simulate_explains_without_calling_upstream(api: Api, world: World) -> None:
    out = await api.ok(
        "POST",
        "/api/v1/route/simulate",
        {"model": "auto:cost", "messages": [{"role": "user", "content": "Solve 3x + 7 = 22"}]},
    )
    assert out["analysis"]["task"] == "math" and out["selected"] == "gpt-pro"
    assert out["baseline_model"] == "claude-pro" and out["routing_us"] > 0
    assert len(out["candidates"]) == 5
    assert (await api.mock_calls()).get("chat", 0) == 0


async def test_quality_table_manual_override(api: Api, world: World) -> None:
    await api.ok(
        "PUT",
        "/api/v1/quality",
        {"model_alias": "gpt-fast", "task": "math", "band": "low", "score": 0.99},
    )
    cells = await api.ok("GET", "/api/v1/quality")
    cell = next(
        c for c in cells if (c["model_alias"], c["task"], c["band"]) == ("gpt-fast", "math", "low")
    )
    assert cell["score"] == 0.99 and cell["source"] == "manual"
    bad = await api.post(
        "/api/v1/quality", {"model_alias": "nope", "task": "math", "band": "low", "score": 1}
    )
    assert bad.status_code in (400, 405)


async def test_system_health_and_metrics(api: Api, world: World) -> None:
    assert (await api.client.get("/healthz")).json() == {"status": "ok"}
    ready = await api.client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["checks"]["postgres"]["ok"]
    info = await api.ok("GET", "/api/v1/system")
    assert info["version"] == "0.1.0" and info["active_keys"] == 1
    r = await api.gateway(
        world.key, {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]
    metrics = (await api.client.get("/metrics")).text
    assert "smartrouter_decisions_total" in metrics and "smartrouter_routing_seconds" in metrics
