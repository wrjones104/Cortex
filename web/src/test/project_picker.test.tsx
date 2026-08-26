import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { ProjectPicker } from "../components/ui";

describe("ProjectPicker", () => {
  it("renders standard dropdown with projects and default option", () => {
    const html = renderToString(
      <ProjectPicker
        value="Work"
        onChange={() => {}}
        projects={["Work", "Personal"]}
      />
    );
    expect(html).toContain('value="Work"');
    expect(html).toContain("Work</option>");
    expect(html).toContain("Personal</option>");
    expect(html).toContain("New project...</option>");
    // Free text input should not be shown when matching known project
    expect(html).not.toContain('placeholder="New project name"');
  });

  it("renders input field when a custom project value is supplied", () => {
    const html = renderToString(
      <ProjectPicker
        value="Bible Notes"
        onChange={() => {}}
        projects={["Work", "Personal"]}
      />
    );
    // Should render input with the custom value intact
    expect(html).toContain('value="Bible Notes"');
    expect(html).toContain('placeholder="New project name"');
  });

  it("renders input field preserving trailing spaces in value", () => {
    const html = renderToString(
      <ProjectPicker
        value="Bible "
        onChange={() => {}}
        projects={["Work", "Personal"]}
      />
    );
    expect(html).toContain('value="Bible "');
  });
});
