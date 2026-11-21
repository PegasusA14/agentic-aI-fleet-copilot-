import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Count } from "./Count";

describe("Count where the browser cannot run number-flow (jsdom)", () => {
  it("renders the number as plain text", () => {
    render(<Count value={1234} />);
    expect(screen.getByText("1,234")).toBeInTheDocument();
  });
});
