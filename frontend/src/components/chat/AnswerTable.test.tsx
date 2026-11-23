import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { numberEvidence, scopeLineText, type SortState } from "../../lib/answerTable";
import { getFixture } from "../../lib/fixtures";
import { setReducedMotion } from "../../test/motion";
import { AnswerTable } from "./AnswerTable";

const MACOS = getFixture("initech-003", "macos_older_than_15").envelope;
const LOW_DISK = getFixture("acme-001", "low_disk").envelope;
const COMPLIANCE = getFixture("acme-001", "compliance_failures").envelope;

function Harness({ envelope = MACOS, reopened = false, onSource = vi.fn() }) {
  const table = envelope.table!;
  const [sort, setSort] = useState<SortState>(table.default_sort);
  return (
    <AnswerTable
      table={table}
      sort={sort}
      onSort={setSort}
      numbered={numberEvidence(envelope)}
      onSource={onSource}
      scope={envelope.scope_line ? scopeLineText(envelope.scope_line, table, envelope.headline) : null}
      reopened={reopened}
    />
  );
}

const hostnames = () =>
  within(screen.getByRole("table")).getAllByRole("rowheader").map((th) => th.querySelector(".atable__name")?.textContent);

describe("the answer table", () => {
  it("is a real table named by its title, with the count beside it", () => {
    render(<Harness />);
    const table = screen.getByRole("table", { name: "Macs behind macOS 15" });
    expect(table.querySelector("caption")).toHaveTextContent("Macs behind macOS 15");
    expect(table.querySelector("caption")).toHaveTextContent("4 devices");
    const heads = within(table).getAllByRole("columnheader");
    expect(heads.map((h) => h.textContent)).toEqual(["Device", "macOS", "Behind 15", "Source"]);
    for (const head of heads) expect(head).toHaveAttribute("scope", "col");
  });

  it("says 'showing 4 of 120' when the table is cut short", () => {
    const envelope = { ...MACOS, table: { ...MACOS.table!, total_rows: 120 } };
    render(<Harness envelope={envelope} />);
    expect(screen.getByRole("table").querySelector("caption")).toHaveTextContent("showing 4 of 120");
  });

  it("puts the hostname first and bold, the serial muted in mono beneath it", () => {
    render(<Harness />);
    const first = within(screen.getByRole("table")).getAllByRole("rowheader")[0];
    expect(first.querySelector(".atable__name")).toHaveTextContent("initech-macbook-2");
    const serial = first.querySelector(".atable__serial");
    expect(serial).toHaveTextContent("SPKRRDF0L786");
    expect(serial).toHaveClass("mono");
    expect(first.textContent?.indexOf("initech-macbook-2")).toBeLessThan(first.textContent?.indexOf("SPKRRDF0L786") ?? 0);
  });

  it("shows a version in mono and a pill with its word and tone", () => {
    render(<Harness />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("13.6")).toHaveClass("mono");
    const attention = within(rows[0]).getByText("2 major behind");
    expect(attention).toHaveClass("apill");
    expect(attention).toHaveAttribute("data-tone", "attention");
    expect(within(rows[2]).getByText("1 major behind")).toHaveAttribute("data-tone", "neutral");
  });

  it("marks an attention value that is not a pill with a word a reader hears, not colour alone", () => {
    render(<Harness envelope={LOW_DISK} />);
    const cell = screen.getByText("2.0%").closest("td") as HTMLElement;
    expect(cell).toHaveAttribute("data-tone", "attention");
    expect(within(cell).getByText(/needs attention/i)).toHaveClass("sr-only");
  });

  it("sorts from a menu: the sorted header says so and the rows move", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const header = () => screen.getByRole("columnheader", { name: "macOS" });
    expect(header()).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByRole("columnheader", { name: "Behind 15" })).not.toHaveAttribute("aria-sort");
    expect(hostnames()).toEqual(["initech-macbook-2", "initech-macbook-7", "initech-macbook-6", "initech-mac-4"]);

    await user.click(screen.getByRole("button", { name: "Sort by: Oldest first" }));
    const menu = screen.getByRole("menu", { name: "Sort order" });
    expect(within(menu).getAllByRole("menuitemradio").map((i) => i.textContent)).toEqual([
      "Oldest first",
      "Newest first",
      "Name, A to Z",
      "Name, Z to A",
    ]);
    expect(within(menu).getByRole("menuitemradio", { name: "Oldest first" })).toHaveAttribute("aria-checked", "true");

    await user.click(within(menu).getByRole("menuitemradio", { name: "Newest first" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(header()).toHaveAttribute("aria-sort", "descending");
    expect(hostnames().slice(0, 2)).toEqual(["initech-macbook-6", "initech-mac-4"]);
    expect(screen.getByRole("button", { name: "Sort by: Newest first" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Sort by: Newest first" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Name, A to Z" }));
    expect(screen.getByRole("columnheader", { name: "Device" })).toHaveAttribute("aria-sort", "ascending");
    expect(header()).not.toHaveAttribute("aria-sort");
    expect(hostnames()).toEqual(["initech-mac-4", "initech-macbook-2", "initech-macbook-6", "initech-macbook-7"]);
  });

  it("drives the sort menu from the keyboard and closes it on Escape", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Sort by: Oldest first" });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menuitemradio", { name: "Oldest first" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitemradio", { name: "Newest first" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
    await user.click(button);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(screen.getByRole("columnheader", { name: "macOS" })).toHaveAttribute("aria-sort", "descending");
  });

  it("gives each row a Source button that names its number and device and reports it", async () => {
    const user = userEvent.setup();
    const onSource = vi.fn();
    render(<Harness onSource={onSource} />);
    const chip = screen.getByRole("button", { name: "Source 1 for initech-macbook-2" });
    expect(chip).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Source 4 for initech-mac-4" })).toBeInTheDocument();
    await user.click(chip);
    expect(onSource).toHaveBeenCalledWith(1, expect.objectContaining({ label: "initech-macbook-2" }));
  });

  it("keeps a row's Source number when the rows are reordered", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Sort by: Oldest first" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Newest first" }));
    expect(screen.getByRole("button", { name: "Source 3 for initech-macbook-6" })).toBeInTheDocument();
  });

  it("writes the scope line under the table, in two lines", () => {
    render(<Harness />);
    const foot = screen.getByRole("table").closest(".atable")?.querySelector(".atable__scope") as HTMLElement;
    const lines = [...foot.querySelectorAll("p")].map((p) => p.textContent);
    expect(lines).toEqual([
      "5 Macs checked · 1 on macOS 15 or later · 2 not checked",
      "Initech · latest snapshot per device · as of 2026-06-12 11:00 UTC",
    ]);
  });

  it("carries a wider table's columns in the answer's order, with each column's kind on its cells", () => {
    render(<Harness envelope={COMPLIANCE} />);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Device",
      "Check",
      "Severity",
      "Latest snapshot",
      "Source",
    ]);
    expect(screen.getAllByText("Medium")[0].closest("td")).toHaveAttribute("data-kind", "pill");
    expect(screen.getAllByText("Medium")[0]).toHaveAttribute("data-tone", "neutral");
  });
});

describe("the rows' arrival", () => {
  it("staggers on first arrival", () => {
    setReducedMotion(false);
    render(<Harness />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLElement[];
    expect(rows[1].style.opacity).toBe("0");
    expect(rows[3].style.opacity).toBe("0");
    setReducedMotion(true);
  });

  it("does not on reopen", () => {
    setReducedMotion(false);
    render(<Harness reopened />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLElement[];
    for (const row of rows) expect(row.style.opacity).not.toBe("0");
    setReducedMotion(true);
  });

  it("does not under reduced motion", () => {
    setReducedMotion(true);
    render(<Harness />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLElement[];
    for (const row of rows) expect(row.style.opacity).not.toBe("0");
  });
});
