from __future__ import annotations

from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.tools import execute
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

ACME, GLOBEX, INITECH = "acme-001", "globex-002", "initech-003"


def context_for(real_db_path, real_conn, tenant=ACME) -> AgentContext:
    return AgentContext(
        db_path=real_db_path,
        company_id=tenant,
        actor_id=f"admin-{tenant.split('-')[0]}",
        as_of=dataset_as_of(real_conn),
    )


def repo_for(real_conn, tenant=ACME) -> FleetRepository:
    return FleetRepository(real_conn, TenantContext(tenant, "agent-test"))


def run(real_db_path, real_conn, tool, args=None, tenant=ACME):
    """Execute one tool the way the agent would, as the given tenant."""
    return execute(tool, args or {}, context_for(real_db_path, real_conn, tenant))
