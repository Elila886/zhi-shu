import { describe, expect, it } from "vitest";
import { documentTypeLabel, fileAcceptValue, filterAllowedFiles, normalizeDocumentExtensions } from "./files";

describe("runtime document extensions", () => {
  it("normalizes, validates, and deduplicates backend values", () => {
    expect(normalizeDocumentExtensions([" .PDF ", ".txt", ".PDF", "docx", "../exe"])).toEqual([".pdf", ".txt"]);
    expect(fileAcceptValue([".PDF", ".txt"])).toBe(".pdf,.txt");
    expect(documentTypeLabel([".pdf", ".docx", ".txt"])).toBe("PDF、DOCX、TXT");
  });

  it("filters selected files using only the runtime allow-list", () => {
    const files = [new File(["a"], "Guide.PDF"), new File(["b"], "notes.txt"), new File(["c"], "script.js")];
    expect(filterAllowedFiles(files, [".pdf", ".txt"]).map((file) => file.name)).toEqual(["Guide.PDF", "notes.txt"]);
    expect(filterAllowedFiles(files, [])).toEqual([]);
  });
});
