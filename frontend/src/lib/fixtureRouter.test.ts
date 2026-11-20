import { describe, expect, it } from "vitest";
import { routeQuestion } from "./fixtureRouter";
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
