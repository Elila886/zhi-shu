export function normalizeDocumentExtensions(extensions: string[]): string[] {
  return [...new Set(extensions.map((extension) => extension.trim().toLowerCase()).filter((extension) => /^\.[a-z0-9]+$/.test(extension)))];
}

export function filterAllowedFiles(files: FileList | File[], extensions: string[]): File[] {
  const allowed = normalizeDocumentExtensions(extensions);
  return Array.from(files).filter((file) => allowed.some((extension) => file.name.toLowerCase().endsWith(extension)));
}

export function fileAcceptValue(extensions: string[]): string {
  return normalizeDocumentExtensions(extensions).join(",");
}

export function documentTypeLabel(extensions: string[]): string {
  return normalizeDocumentExtensions(extensions).map((extension) => extension.slice(1).toUpperCase()).join("、");
}
