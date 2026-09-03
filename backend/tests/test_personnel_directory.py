import json
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from app.chat.langgraph_agent import build_retrival_graph
from app.chat.tools import query_personnel, tools
from app.db.main import async_session
from app.db.models import AuditLog, PersonnelProfile, User
from app.personnel import service as personnel_service


PASSWORD = "Personnel123!"


class PersonnelThenRagModel:
    """A deliberately unsafe model response used to prove the graph fence."""

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages):
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "query_personnel", "args": {"person_name": "张三"}, "id": "personnel-1"},
                {"name": "retrieve_user_documents", "args": {"query": "张三"}, "id": "rag-1"},
            ],
        )


async def signup(client: AsyncClient, name: str) -> dict:
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "username": name,
            "email": f"{name}@example.com",
            "password": PASSWORD,
            "first_name": "Test",
            "last_name": "User",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


async def login(client: AsyncClient, name: str, *, admin: bool = False) -> dict:
    prefix = "/api/v1/auth/admin" if admin else "/api/v1/auth"
    response = await client.post(f"{prefix}/login", data={"username": f"{name}@example.com", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(payload: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {payload['access_token']}"}


async def set_role(email: str, role: str) -> None:
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.role = role
        await session.commit()


def profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": "张 三",
        "employee_no": "E-1001",
        "department": "工程部",
        "job_title": "后端工程师",
        "work_email": "zhangsan@work.example.com",
        "work_phone": "010-12345678",
        "employment_status": "active",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_super_admin_maintains_profiles_and_only_admins_can_receive_query_permission(client: AsyncClient):
    root = await signup(client, "rootpersonnel")
    manager = await signup(client, "managerpersonnel")
    employee = await signup(client, "employeeone")
    duplicate_employee = await signup(client, "employeetwo")
    await set_role("rootpersonnel@example.com", "super_admin")
    await set_role("managerpersonnel@example.com", "admin")
    root_login = await login(client, "rootpersonnel", admin=True)
    manager_login = await login(client, "managerpersonnel", admin=True)

    assert (await client.get(f"/api/v1/admin/users/{employee['id']}/personnel-profile", headers=bearer(manager_login))).status_code == 403
    missing = await client.get(f"/api/v1/admin/users/{employee['id']}/personnel-profile", headers=bearer(root_login))
    assert missing.status_code == 200
    assert missing.json() == {"profile": None, "can_query_personnel": False}

    saved = await client.put(f"/api/v1/admin/users/{employee['id']}/personnel-profile", headers=bearer(root_login), json=profile_payload())
    assert saved.status_code == 200, saved.text
    assert saved.json()["full_name"] == "张 三"
    duplicate_no = await client.put(
        f"/api/v1/admin/users/{duplicate_employee['id']}/personnel-profile",
        headers=bearer(root_login),
        json=profile_payload(full_name="李四"),
    )
    assert duplicate_no.status_code == 409

    invalid_grant = await client.patch(
        f"/api/v1/admin/users/{employee['id']}/personnel-query-permission",
        headers=bearer(root_login),
        json={"enabled": True},
    )
    assert invalid_grant.status_code == 400
    grant = await client.patch(
        f"/api/v1/admin/users/{manager['id']}/personnel-query-permission",
        headers=bearer(root_login),
        json={"enabled": True},
    )
    assert grant.status_code == 200
    assert grant.json()["can_query_personnel"] is True
    demoted = await client.patch(
        f"/api/v1/admin/users/{manager['id']}",
        headers=bearer(root_login),
        json={"role": "user", "is_active": True},
    )
    assert demoted.status_code == 200
    after_demotion = await client.get(f"/api/v1/admin/users/{manager['id']}/personnel-profile", headers=bearer(root_login))
    assert after_demotion.json()["can_query_personnel"] is False


@pytest.mark.asyncio
async def test_directory_query_is_live_authorized_normalized_and_audited(client: AsyncClient):
    root = await signup(client, "rootquery")
    manager = await signup(client, "managerquery")
    employee = await signup(client, "employeequery")
    await set_role("rootquery@example.com", "super_admin")
    await set_role("managerquery@example.com", "admin")
    root_login = await login(client, "rootquery", admin=True)
    await client.put(f"/api/v1/admin/users/{employee['id']}/personnel-profile", headers=bearer(root_login), json=profile_payload(full_name=" 张　三 "))
    await client.patch(f"/api/v1/admin/users/{manager['id']}/personnel-query-permission", headers=bearer(root_login), json={"enabled": True})

    tool_result = json.loads(
        await query_personnel.ainvoke(
            {"person_name": "张 三"},
            config={"configurable": {"user_id": manager["id"], "client_ip": "127.0.0.1"}},
        )
    )
    assert tool_result["status"] == "found"
    assert tool_result["profile"] == {
        "full_name": "张 三",
        "employee_no": "E-1001",
        "department": "工程部",
        "job_title": "后端工程师",
        "work_email": "zhangsan@work.example.com",
        "work_phone": "010-12345678",
        "employment_status": "active",
    }

    async with async_session() as session:
        manager_model = await session.get(User, UUID(manager["id"]))
        assert manager_model is not None
        manager_model.can_query_personnel = False
        await session.commit()
    denied = json.loads(await query_personnel.ainvoke({"person_name": "张 三"}, config={"configurable": {"user_id": manager["id"]}}))
    assert denied == {"event": "personnel_query", "status": "forbidden"}

    async with async_session() as session:
        audits = list((await session.execute(select(AuditLog).where(AuditLog.action.like("personnel.query.%")))).scalars())
    assert {audit.action for audit in audits} >= {"personnel.query.found", "personnel.query.denied"}
    assert all("zhangsan@work.example.com" not in json.dumps(audit.after_data or {}) for audit in audits)
    assert query_personnel in tools


@pytest.mark.asyncio
async def test_duplicate_names_are_disambiguated_and_profile_deletes_with_user(client: AsyncClient):
    root = await signup(client, "rootduplicate")
    manager = await signup(client, "managerduplicate")
    first = await signup(client, "firstduplicate")
    second = await signup(client, "secondduplicate")
    await set_role("rootduplicate@example.com", "super_admin")
    await set_role("managerduplicate@example.com", "admin")
    root_login = await login(client, "rootduplicate", admin=True)
    for user, number, department in ((first, "E-2001", "产品部"), (second, "E-2002", "销售部")):
        response = await client.put(
            f"/api/v1/admin/users/{user['id']}/personnel-profile",
            headers=bearer(root_login),
            json=profile_payload(full_name="Alex Lee", employee_no=number, department=department, work_email=None, work_phone=None, employment_status="inactive"),
        )
        assert response.status_code == 200, response.text
    await client.patch(f"/api/v1/admin/users/{manager['id']}/personnel-query-permission", headers=bearer(root_login), json={"enabled": True})

    async with async_session() as session:
        duplicate = await personnel_service.query_directory(
            session=session,
            actor_id=UUID(manager["id"]),
            person_name="  alex   lee ",
        )
    assert duplicate["status"] == "ambiguous"
    assert duplicate["candidates"] == [
        {"employee_no": "E-2001", "department": "产品部"},
        {"employee_no": "E-2002", "department": "销售部"},
    ]

    async with async_session() as session:
        user = await session.get(User, UUID(first["id"]))
        assert user is not None
        await session.delete(user)
        await session.commit()
        assert await session.get(PersonnelProfile, UUID(first["id"])) is None


@pytest.mark.asyncio
async def test_personnel_graph_does_not_execute_rag_after_a_personnel_tool_call(client: AsyncClient):
    manager = await signup(client, "managergraph")
    await set_role("managergraph@example.com", "admin")
    graph = build_retrival_graph(
        MemorySaver(),
        "test-model",
        model_override=PersonnelThenRagModel(),  # type: ignore[arg-type]
        tool_override=[query_personnel],
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="查询张三的基本信息")]},
        config={"configurable": {"user_id": manager["id"], "thread_id": str(uuid4())}},
    )

    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert [message.name for message in tool_messages] == ["query_personnel"]
    assert result["messages"][-1].content == "你没有查询人员基本信息的权限。"
