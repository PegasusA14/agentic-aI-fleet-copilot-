import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UserMessage } from "./UserMessage";

describe("UserMessage", () => {
  it("shows the question as plain text in a right-aligned bubble", () => {
    const { container } = render(<UserMessage text="Which devices are low on disk space?" />);
    expect(screen.getByText("Which devices are low on disk space?")).toBeInTheDocument();
    expect(container.querySelector(".msg--user")).not.toBeNull();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("never renders the text as markup and keeps line breaks", () => {
    const { container } = render(<UserMessage text={"<i>hi</i>\nsecond line"} />);
    expect(container.querySelector("i")).toBeNull();
    expect(container.textContent).toContain("<i>hi</i>");
  });
});
