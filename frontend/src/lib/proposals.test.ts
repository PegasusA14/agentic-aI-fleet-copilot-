import { describe, expect, it } from "vitest";
import { decidedSheet, scopeFacts } from "./proposals";

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

describe("decidedSheet", () => {
  const card = { action_type: "remediation_ticket", target_label: "acme-macbook-2" } as const;

  it("words the chip and headline for each decision, in code-written text", () => {
    expect(decidedSheet("executed", card)).toMatchObject({
      word: "Executed (simulated)",
      tone: "pass",
      headline: "Executed (simulated): Remediation ticket for acme-macbook-2.",
    });
    expect(decidedSheet("rejected", card)?.headline).toBe("Rejected: no action was taken.");
    expect(decidedSheet("invalidated", card)?.headline).toBe("Not executed: the evidence no longer supports it.");
    expect(decidedSheet("failed", card)?.headline).toBe("Not executed: the simulated action failed.");
  });

  it("leaves a proposal that is still waiting as the server worded it", () => {
    expect(decidedSheet("pending", card)).toBeNull();
  });
});
