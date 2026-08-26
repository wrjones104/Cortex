import { useMemo, type MouseEvent } from "react";
import { Marked, type Tokens } from "marked";

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

const marked = new Marked({
  gfm: true,
  breaks: true,
});

marked.use({
  renderer: {
    code({ text, lang }: Tokens.Code) {
      const language = (lang || "code").trim().split(/\s+/)[0];
      const encodedCode = encodeURIComponent(text);
      return `<div class="code-block"><div class="code-header"><span class="code-lang">${escapeHtml(language)}</span><button type="button" class="code-copy-btn" data-code="${encodedCode}" aria-label="Copy code">Copy</button></div><pre><code class="language-${escapeHtml(language)}">${escapeHtml(text)}</code></pre></div>`;
    },
    table(token: Tokens.Table) {
      const headerCells = token.header
        .map((cell) => `<th>${this.parser.parseInline(cell.tokens)}</th>`)
        .join("");
      const rowsHtml = token.rows
        .map(
          (row) =>
            `<tr>${row.map((cell) => `<td>${this.parser.parseInline(cell.tokens)}</td>`).join("")}</tr>`,
        )
        .join("");
      return `<div class="table-wrapper"><table class="markdown-table"><thead><tr>${headerCells}</tr></thead><tbody>${rowsHtml}</tbody></table></div>`;
    },
    link({ href, title, tokens }: Tokens.Link) {
      const safeHref = href.startsWith("javascript:") ? "#" : href;
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
      const textHtml = this.parser.parseInline(tokens);
      return `<a href="${escapeHtml(safeHref)}"${titleAttr} target="_blank" rel="noopener noreferrer" class="markdown-link">${textHtml}</a>`;
    },
  },
});

export function Markdown({
  content,
  className = "",
}: {
  content: string;
  className?: string;
}) {
  const html = useMemo(() => {
    if (!content) return "";
    try {
      const parsed = marked.parse(content);
      return typeof parsed === "string" ? parsed : "";
    } catch {
      return escapeHtml(content);
    }
  }, [content]);

  function handleContainerClick(event: MouseEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement | null;
    const button = target?.closest(".code-copy-btn") as HTMLButtonElement | null;
    if (!button) return;

    const encoded = button.getAttribute("data-code");
    if (!encoded) return;

    const rawCode = decodeURIComponent(encoded);
    void navigator.clipboard.writeText(rawCode).then(() => {
      const originalText = button.textContent;
      button.textContent = "Copied! ✨";
      button.classList.add("copied");
      setTimeout(() => {
        button.textContent = originalText;
        button.classList.remove("copied");
      }, 2000);
    });
  }

  return (
    <div
      className={`markdown-content ${className}`}
      onClick={handleContainerClick}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
