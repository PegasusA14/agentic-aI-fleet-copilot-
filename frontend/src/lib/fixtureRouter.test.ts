import { describe, expect, it } from "vitest";
import { routeQuestion } from "./fixtureRouter";
import { HOME_SUGGESTIONS } from "./suggestions";
import { FIXTURE_INDEX, TENANTS, fixturesFor, getFixture } from "./fixtures";

const scenario = (tenant: string, question: string) => routeQuestion(tenant, question).meta.scenario;

describe("routeQuestion", () => {
  it("sends each plain-English question to the matching real answer", () => {
    const acme = "acme-001";
    expect(scenario(acme, "Which devices are low on disk space?")).toBe("low_disk");
    expect(scenario(acme, "which macs run a macOS version older than 15")).toBe("macos_older_than_15");
    expect(scenario(acme, "Which devices fail a compliance check?")).toBe("compliance_failures");
    expect(scenario(acme, "Are any high-severity compliance checks failing?")).toBe(
      "compliance_high_no_match",
    );
    expect(scenario(acme, "Which devices are using too much memory right now?")).toBe("memory_pressure");
    expect(scenario(acme, "Which devices have had low disk space all week?")).toBe("disk_trend");
    expect(scenario(acme, "Is memory use trending up over the last day?")).toBe("insufficient_trend");
    expect(scenario(acme, "Which laptops show battery wear?")).toBe("battery_wear");
    expect(scenario(acme, "How many Macs do we have, by model?")).toBe("inventory_by_model");
    expect(scenario(acme, "Open a ticket for the first one")).toBe("proposal");
    expect(scenario(acme, "What's the weather in Berlin?")).toBe("unsupported");
    expect(scenario(acme, "Which devices have a weak Wi-Fi signal?")).toBe("unsupported_wifi");
  });

  it("answers each shorter home-screen suggestion with the real answer it stands for", () => {
    const expected = ["low_disk", "macos_older_than_15", "compliance_failures"];
    HOME_SUGGESTIONS.forEach((question, i) => {
      for (const tenant of TENANTS) expect(scenario(tenant.id, question)).toBe(expected[i]);
    });
  });

  it("refuses a device of another company exactly like any unknown device", () => {
    const foreign = getFixture("acme-001", "device_not_found").meta.keywords[0];
    const fixture = routeQuestion("acme-001", `Show the disk history for device ${foreign.toUpperCase()}`);
    expect(fixture.envelope.outcome).toBe("denied");
  });

  it("asks for a detail when nothing matches, instead of guessing", () => {
    expect(scenario("acme-001", "Which devices need attention?")).toBe("clarification");
    expect(scenario("acme-001", "")).toBe("clarification");
  });

  it("answers from the signed-in company's own fixtures only", () => {
    for (const tenant of TENANTS) {
      for (const meta of FIXTURE_INDEX[tenant.id]) {
        const fixture = routeQuestion(tenant.id, meta.question);
        expect(fixture.envelope.scope.company_id).toBe(tenant.id);
      }
    }
  });

  it("routes every stored question back to its own scenario", () => {
    for (const tenant of TENANTS) {
      for (const fixture of fixturesFor(tenant.id)) {
        expect(routeQuestion(tenant.id, fixture.meta.question).meta.scenario).toBe(
          fixture.meta.scenario,
        );
      }
    }
  });
});

describe("advice in replay mode", () => {
  it("answers 'what should I do?' with a labelled stand-in built from the real low-disk devices", () => {
    const fixture = routeQuestion("acme-001", "So what should I do?");
    expect(fixture.meta.scenario).toBe("advise");
    expect(fixture.meta.synthetic).toBe(true);
    expect(fixture.meta.note).toMatch(/offline/i);
    const actions = fixture.envelope.suggested_actions;
    expect(actions.map((a) => a.device_id)).toEqual(["MT7PJB7N5LRE", "8NM23J95R5I6"]);
    expect(actions[0].label).toBe("Order a storage upgrade (512 GiB → 1 TiB)");
    expect(actions[1].label).toBe("Order a storage upgrade (1 TiB → 2 TiB)");
    expect(actions[0].request).toBe("Order a storage upgrade for acme-macbook-4");
    for (const action of actions) expect(fixture.envelope.evidence.map((e) => e.ref)).toContain(action.refs[0]);
    expect(fixture.envelope.proposal).toBeNull();
    expect(fixture.trace.at(-1)?.kind).toBe("respond");
  });

  it("answers every company's advice from its own devices", () => {
    for (const tenant of TENANTS) {
      const fixture = routeQuestion(tenant.id, "any recommendations?");
      expect(fixture.meta.scenario).toBe("advise");
      expect(fixture.envelope.scope.company_id).toBe(tenant.id);
      expect(fixture.envelope.suggested_actions.length).toBeGreaterThan(0);
    }
  });

  it("turns a suggestion's request into a labelled storage-upgrade proposal for that device", () => {
    const fixture = routeQuestion("acme-001", "Order a storage upgrade for acme-thinkpad-8");
    expect(fixture.envelope.outcome).toBe("proposal");
    expect(fixture.envelope.proposal).toMatchObject({
      action_type: "upgrade_order",
      target_device_id: "8NM23J95R5I6",
      status: "pending",
      scope: { component: "storage", current: "1 TiB", target: "2 TiB" },
    });
    expect(fixture.meta.synthetic).toBe(true);
  });

  it("does not take another company's device name", () => {
    expect(routeQuestion("acme-001", "Order a storage upgrade for globex-thinkpad-8").meta.scenario).not.toBe("upgrade");
  });
});
