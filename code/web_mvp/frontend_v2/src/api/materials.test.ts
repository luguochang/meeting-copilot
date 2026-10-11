import { describe, expect, it, vi } from "vitest";
import { HttpMaterialsApi } from "./materials";
import { HttpMeetingApi } from "./client";
import { isTranscriptEvidence, parseDocumentSourceEvidence } from "./sourceEvidence";
function response(value: unknown) { return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } }); }
describe("material transport and typed evidence", () => {
  it("keeps attachments separate from project and transcript identities", () => {
    const value = parseDocumentSourceEvidence({ source_type: "meeting_attachment", source_id: "span", set_id: "set", meeting_id: "meeting", attachment_id: "attachment", title: "需求", version_id: "v1", version: 1, content_hash: "hash", locator: { page: 3 }, quote: "通知同步", context_revision: 2, ordinal: 51 });
    expect(value.sourceType).toBe("meeting_attachment"); expect(isTranscriptEvidence(value)).toBe(false);
    expect(value).not.toHaveProperty("projectId"); expect(value).not.toHaveProperty("documentId");
  });
  it("opening an empty set is read-only and create+bind carries a stable idempotency key", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ material_set: null })).mockResolvedValueOnce(response({ meeting_id: "meeting", created: true })); vi.stubGlobal("fetch", fetcher);
    expect(await new HttpMaterialsApi("http://localhost:9000").getForMeeting("meeting")).toBeNull();
    expect(fetcher.mock.calls[0][1].method).toBeUndefined();
    await new HttpMeetingApi("http://localhost:9000").createMeeting("meeting", "评审", "microphone", undefined, { materialSetId: "set", expectedMaterialSetRevision: 2, idempotencyKey: "fixed-key" });
    expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).toBe("fixed-key");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({ meeting_id: "meeting", material_set_id: "set", expected_material_set_revision: 2 });
  });
});
