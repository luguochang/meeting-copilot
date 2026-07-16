export function segmentDomId(segmentId: string): string {
  return `segment-${segmentId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}
