import { describe, expect, it } from "vitest";
import { actionSentence, decidedSheet, scopeFacts, STATUS_STYLE } from "./proposals";

describe("scopeFacts", () => {
  it("words the check, topic and component codes and keeps ids as tokens", () => {
    const facts = scopeFacts({
      check_id: "os_up_to_date",
      topic: "memory_pressure",
      component: "storage",
      device: "1LYSSFD074BB",
      severity: "medium",
    });
    const value = (key: string) => facts.find((f) => f.key === key);
    expect(value("check_id")?.value).toBe("OS up to date");
    expect(value("topic")?.value).toBe("Memory pressure");
    expect(value("component")?.value).toBe("Storage");
    expect(value("device")).toMatchObject({ value: "1LYSSFD074BB", token: true });
    expect(value("severity")?.value).toBe("medium");
  });

  it("covers every known code and still reads an unknown one as words", () => {
    const words = (key: string, code: string) => scopeFacts({ [key]: code })[0].value;
    expect(words("check_id", "screen_lock")).toBe("Screen lock");
    expect(words("check_id", "disk_encryption")).toBe("Disk encryption");
    expect(words("topic", "low_disk")).toBe("Low disk");
    expect(words("topic", "failing_check")).toBe("Failing check");
    expect(words("topic", "battery_service")).toBe("Battery service");
    expect(words("component", "memory")).toBe("Memory");
    expect(words("check_id", "new_thing")).toBe("New thing");
  });
});

describe("scopeFacts on a proposal", () => {
  it("leaves out the device when the card already names it, and keeps it when it differs", () => {
    const keys = (scope: Record<string, string>, target?: string) => scopeFacts(scope, target).map((f) => f.key);
    expect(keys({ device: "1LYSSFD074BB", severity: "medium" }, "1LYSSFD074BB")).toEqual(["severity"]);
    expect(keys({ device: "1LYSSFD074BB", severity: "medium" })).toEqual(["device", "severity"]);
    expect(keys({ device: "OTHER", severity: "medium" }, "1LYSSFD074BB")).toEqual(["device", "severity"]);
  });

  it("merges Current and Target into one Change fact", () => {
    const facts = scopeFacts({ device: "D1", component: "storage", current: "512 GiB", target: "1 TiB" }, "D1");
    expect(facts.map((f) => f.label)).toEqual(["Component", "Change"]);
    expect(facts[1]).toMatchObject({ value: "512 GiB → 1 TiB", token: false });
  });

  it("keeps a lone Current or Target as it is", () => {
    expect(scopeFacts({ current: "512 GiB" }).map((f) => f.label)).toEqual(["Current"]);
  });
});

describe("actionSentence", () => {
  const upgrade = { action_type: "upgrade_order", target_label: "acme-thinkpad-8" } as const;

  it("names the component an upgrade is for", () => {
    expect(actionSentence({ ...upgrade, scope: { component: "storage" } })).toBe("Order a storage upgrade for acme-thinkpad-8");
    expect(actionSentence({ ...upgrade, scope: { component: "memory" } })).toBe("Order a memory upgrade for acme-thinkpad-8");
  });

  it("says hardware only when the proposal names no component", () => {
    expect(actionSentence(upgrade)).toBe("Order a hardware upgrade for acme-thinkpad-8");
    expect(actionSentence({ ...upgrade, scope: {} })).toBe("Order a hardware upgrade for acme-thinkpad-8");
  });

  it("words the other actions as before", () => {
    expect(actionSentence({ action_type: "remediation_ticket", target_label: "d" })).toBe("Open a remediation ticket for d");
    expect(actionSentence({ action_type: "replacement_flag", target_label: "d" })).toBe("Flag d for replacement");
  });
});

describe("a pending proposal's state", () => {
  it("reads as waiting for the person, not as an unexplained 'Pending'", () => {
    expect(STATUS_STYLE.pending.word).toBe("Awaiting your approval");
  });
});

describe("decidedSheet", () => {
  const card = { action_type: "remediation_ticket", target_label: "acme-macbook-2" } as const;

  it("words the headline for each decision, in code-written text", () => {
    expect(decidedSheet("executed", card)).toEqual({
      headline: "Executed (simulated): Remediation ticket for acme-macbook-2.",
    });
    expect(
      decidedSheet("executed", { action_type: "upgrade_order", target_label: "d", scope: { component: "storage" } })?.headline,
    ).toBe("Executed (simulated): Storage upgrade order for d.");
    expect(decidedSheet("rejected", card)?.headline).toBe("Rejected: no action was taken.");
    expect(decidedSheet("invalidated", card)?.headline).toBe("Not executed: the evidence no longer supports it.");
    expect(decidedSheet("failed", card)?.headline).toBe("Not executed: the simulated action failed.");
  });

  it("leaves a proposal that is still waiting as the server worded it", () => {
    expect(decidedSheet("pending", card)).toBeNull();
  });
});
