import { describe, expect, it } from "vitest";
import { Marked } from "marked";

describe("Markdown parsing", () => {
  const marked = new Marked({ gfm: true, breaks: true });

  it("parses headings and lists", () => {
    const input = "# Heading 1\n- Item 1\n- Item 2";
    const html = marked.parse(input) as string;
    expect(html).toContain("<h1>Heading 1</h1>");
    expect(html).toContain("<ul>");
    expect(html).toContain("<li>Item 1</li>");
  });

  it("parses code fences and bold text", () => {
    const input = "**Bold text** and `inline code`";
    const html = marked.parse(input) as string;
    expect(html).toContain("<strong>Bold text</strong>");
    expect(html).toContain("<code>inline code</code>");
  });

  it("parses tables", () => {
    const input = "| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | Cell 2 |";
    const html = marked.parse(input) as string;
    expect(html).toContain("<table>");
    expect(html).toContain("<th>Header 1</th>");
    expect(html).toContain("<td>Cell 1</td>");
  });
});
