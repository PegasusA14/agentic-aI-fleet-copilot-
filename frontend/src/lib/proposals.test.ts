import { describe, expect, it } from "vitest";
import { actionSentence, decidedSheet, proposalFields, proposalLead, proposalResult, riskNote, scopeFacts, STATUS_STYLE } from "./proposals";
import type { ProposalCard } from "./types";

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
    expect(STATUS_STYLE.pending.tone).toBe("attention");
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

describe("proposalLead", () => {
  const base = {
    company_id: "c",
    evidence_refs: ["r"] as ProposalCard["evidence_refs"],
    expected_effect: "",
    payload_hash: "h".repeat(64),
    proposal_id: "p1",
    risk: "",
    status: "pending" as const,
    target_device_id: "SERIAL1",
    target_label: "initech-macbook-2",
    rationale: "Memory use stayed above 90% in 24 of the last 30 snapshots. Disk and battery are healthy.",
  };

  it("says what the copilot would do, the first sentence of why, and that nothing is sent yet", () => {
    const lead = proposalLead({
      ...base,
      action_type: "upgrade_order",
      scope: { device: "SERIAL1", component: "memory", current: "16 GB", target: "32 GB" },
    });
    expect(lead.lead).toBe("I'd order a memory upgrade for initech-macbook-2.");
    expect(lead.support).toBe("Memory use stayed above 90% in 24 of the last 30 snapshots. Nothing is sent until you approve it.");
  });

  it.each([
    [{ action_type: "remediation_ticket", scope: { check_id: "os_up_to_date", severity: "medium" } }, "I'd open an OS update ticket for initech-macbook-2."],
    [{ action_type: "remediation_ticket", scope: { check_id: "screen_lock" } }, "I'd open a screen lock ticket for initech-macbook-2."],
    [{ action_type: "remediation_ticket", scope: {} }, "I'd open a remediation ticket for initech-macbook-2."],
    [{ action_type: "replacement_flag", scope: { reason: "battery_service_recommended" } }, "I'd flag initech-macbook-2 for replacement."],
    [{ action_type: "employee_notification", scope: { topic: "low_disk" } }, "I'd notify the employee assigned to initech-macbook-2."],
  ] as const)("opens each action in its own words: %#", (change, expected) => {
    expect(proposalLead({ ...base, ...change } as unknown as ProposalCard).lead).toBe(expected);
  });

  it("puts check codes in the rationale into words and ends a rationale that has no full stop", () => {
    const lead = proposalLead({
      ...base,
      action_type: "remediation_ticket",
      scope: { check_id: "screen_lock" },
      rationale: "screen_lock reports fail with medium severity",
    });
    expect(lead.support).toBe("Screen lock reports fail with medium severity. Nothing is sent until you approve it.");
  });
});

describe("proposalFields", () => {
  const card = (action_type: ProposalCard["action_type"], scope: Record<string, string>) =>
    ({ action_type, scope, target_label: "host-1", target_device_id: "SER1" }) as unknown as ProposalCard;
  const labels = (c: ProposalCard) => proposalFields(c).map((f) => f.label);

  it("puts the device first with its serial, then the fields for the action", () => {
    expect(proposalFields(card("upgrade_order", { component: "memory", current: "16 GB", target: "32 GB" }))).toEqual([
      { key: "device", label: "Device", value: "host-1", sub: "SER1" },
      { key: "component", label: "Component", value: "Memory" },
      { key: "spec", label: "Spec", value: "16 GB → 32 GB" },
    ]);
    expect(labels(card("remediation_ticket", { check_id: "screen_lock", severity: "high" }))).toEqual(["Device", "Check", "Severity"]);
    expect(labels(card("replacement_flag", { reason: "battery_service_recommended" }))).toEqual(["Device", "Reason"]);
    expect(labels(card("employee_notification", { topic: "low_disk" }))).toEqual(["Device", "Recipient", "Topic"]);
  });

  it("leaves out the serial when the label already is the serial", () => {
    const same = { ...card("replacement_flag", {}), target_label: "SER1" };
    expect(proposalFields(same)[0].sub).toBeUndefined();
  });
});

describe("riskNote and proposalResult", () => {
  const card = { action_type: "upgrade_order", target_label: "h1", risk: "", scope: { component: "memory", current: "16 GB", target: "32 GB" } } as unknown as ProposalCard;

  it("uses the card's own note, else a true sentence", () => {
    expect(riskNote({ ...card, risk: "Affects only h1 in c." })).toBe("Affects only h1 in c.");
    expect(riskNote(card)).toBe("Affects only h1. Nothing leaves this system. The decision is recorded in Audit.");
  });

  it("reads each decision as a result with a mark, a title and one line", () => {
    expect(proposalResult(card, "executed", true)).toMatchObject({
      icon: "check",
      tone: "pass",
      title: "Upgrade order approved",
      line: "Memory 16 GB → 32 GB on h1 · approved by you · simulated",
    });
    expect(proposalResult(card, "executed", false)?.line).toBe("Memory 16 GB → 32 GB on h1 · approved · simulated");
    expect(proposalResult(card, "rejected", true)).toMatchObject({ icon: "x", title: "Upgrade order rejected", line: "Nothing was changed. The rejection is logged." });
    expect(proposalResult(card, "invalidated", false)).toMatchObject({ icon: "warning", title: "No longer valid" });
    expect(proposalResult(card, "pending", false)).toBeNull();
  });
});
