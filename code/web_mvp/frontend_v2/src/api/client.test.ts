import { fetchProviderStatus, HttpMeetingApi } from "./client";
import {
  ContractError,
  parseMeetingSnapshot,
  parseReviewDocument,
  parseReviewDocumentRevisions,
  reconcileProviderStatus,
} from "./schema";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("HttpMeetingApi", () => {
  it("parses the provider status contract and rejects a stale probe after a model switch", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({
      configured: true,
      runtime_synced: true,
      probe_status: "succeeded",
      model: "gpt-old",
      realtime_model: "gpt-realtime-old",
    }));
    vi.stubGlobal("fetch", fetchSpy);

    const runtime = await fetchProviderStatus();

    expect(runtime).toEqual({
      configured: true,
      runtime_synced: true,
      probe_status: "succeeded",
      model: "gpt-old",
      realtime_model: "gpt-realtime-old",
    });
    expect(reconcileProviderStatus({
      configured: true,
      runtime_synced: true,
      model: "gpt-old",
      realtime_model: "gpt-realtime-new",
    }, runtime)).toEqual({
      configured: true,
      runtime_synced: false,
      probe_status: "not_run",
      model: "gpt-old",
      realtime_model: "gpt-realtime-new",
    });
    expect(reconcileProviderStatus({
      configured: true,
      runtime_synced: true,
      model: "gpt-new",
      realtime_model: "gpt-realtime-old",
    }, runtime)).toMatchObject({
      runtime_synced: false,
      probe_status: "not_run",
      model: "gpt-new",
      realtime_model: "gpt-realtime-old",
    });
    expect(fetchSpy).toHaveBeenCalledWith("/providers/status", expect.objectContaining({ method: "GET" }));
  });

  it("parses the two recording tracks and keeps a partial failure explicit", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({
      meeting_id: "meeting/dual",
      status: "partial_failure",
      assembled: false,
      playback_url: null,
      format: null,
      file_size_bytes: 0,
      chunk_count: 3,
      duration_ms: 120_000,
      tracks: ["microphone", "system_audio"],
      chunks: [],
      track_states: [
        {
          track_id: "microphone",
          source: "microphone",
          epoch: 2,
          status: "ready",
          duration_ms: 120_000,
          playback_url: "/v2/meetings/meeting%2Fdual/audio/tracks/microphone/content?epoch=2",
          error_class: null,
        },
        {
          track_id: "system_audio",
          source: "system_audio",
          epoch: 2,
          status: "failed",
          duration_ms: 0,
          playback_url: null,
          error_class: "screen_capture_permission_denied",
        },
      ],
      derived_assets: [],
      mixed_create_url: "/v2/meetings/meeting%2Fdual/audio/mixed",
    }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await expect(api.getAudio("meeting/dual")).resolves.toMatchObject({
      overallStatus: "partial_failure",
      trackStates: [
        expect.objectContaining({ trackId: "microphone", status: "ready", durationMs: 120_000 }),
        expect.objectContaining({
          trackId: "system_audio",
          status: "failed",
          errorClass: "screen_capture_permission_denied",
        }),
      ],
      mixedCreateUrl: "/v2/meetings/meeting%2Fdual/audio/mixed",
    });
  });

  it("creates an explicit local mixed replay without replacing the source tracks", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({
      asset: {
        asset_id: "mixed-1",
        meeting_id: "meeting/dual",
        kind: "mixed",
        derivation: "local_pcm16_timeline_mix",
        status: "ready",
        sources: [
          { track_id: "microphone", epoch: 0 },
          { track_id: "system_audio", epoch: 0 },
        ],
        duration_ms: 121_000,
        playback_url: "/v2/meetings/meeting%2Fdual/audio/mixed/mixed-1/content",
        remote_upload_used: false,
      },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await expect(api.createMixedAudio("meeting/dual")).resolves.toMatchObject({
      assetId: "mixed-1",
      kind: "mixed",
      playbackUrl: "/v2/meetings/meeting%2Fdual/audio/mixed/mixed-1/content",
      remoteUploadUsed: false,
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting%2Fdual/audio/mixed",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("creates the durable V2 meeting before audio capture starts", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting: { id: "rec_new" } }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.createMeeting("rec_new");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          meeting_id: "rec_new",
          expected_duration_seconds: 3_600,
          track_count: 1,
        }),
      }),
    );
  });

  it("declares two recording tracks when creating a dual-track meeting", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting: { id: "rec_dual" } }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.createMeeting("rec_dual", "双轨访谈", "dual_track");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          meeting_id: "rec_dual",
          expected_duration_seconds: 3_600,
          track_count: 2,
          title: "双轨访谈",
        }),
      }),
    );
  });

  it("saves meeting preparation before capture starts", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting_id: "meeting-1" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.saveMeetingPreparation("meeting/1", {
      hotwords: ["P99", "checkout-service"],
      inputSource: "microphone",
      inputDeviceId: "mic-1",
      inputDeviceName: "MacBook Microphone",
      noticeAcknowledged: true,
    });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting%2F1/preparation",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          hotwords: ["P99", "checkout-service"],
          input_source: "microphone",
          input_device_id: "mic-1",
          input_device_name: "MacBook Microphone",
          notice_acknowledged: true,
        }),
      }),
    );
  });

  it("persists dual-track as the meeting preparation source", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting_id: "meeting-dual" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.saveMeetingPreparation("meeting-dual", {
      hotwords: ["ScreenCaptureKit"],
      inputSource: "dual_track",
      inputDeviceId: null,
      inputDeviceName: "麦克风 + 系统音频",
      noticeAcknowledged: true,
    });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting-dual/preparation",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          hotwords: ["ScreenCaptureKit"],
          input_source: "dual_track",
          input_device_id: null,
          input_device_name: "麦克风 + 系统音频",
          notice_acknowledged: true,
        }),
      }),
    );
  });

  it("uploads a recording as multipart without overriding the browser boundary", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting_id: "imported-meeting" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();
    const file = new File(["audio"], "meeting.m4a", { type: "audio/mp4" });

    await expect(api.importRecording(file)).resolves.toEqual({ meetingId: "imported-meeting", job: null });

    const request = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(fetchSpy.mock.calls[0][0]).toBe("/v2/meetings/import-audio");
    expect(request.method).toBe("POST");
    expect(request.headers).toEqual({ Accept: "application/json" });
    expect(request.body).toBeInstanceOf(FormData);
  });

  it("retries a durable recording import job through the meeting endpoint", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({
      import_job: {
        id: "import-job-1",
        meeting_id: "meeting/1",
        status: "pending",
        stage: "reading",
        progress: 0,
        retryable: false,
      },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await expect(api.retryImportJob("meeting/1")).resolves.toMatchObject({
      id: "import-job-1",
      meetingId: "meeting/1",
      status: "pending",
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting%2F1/import-job/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("sends the user-provided meeting title with the import job", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({
      meeting_id: "imported-meeting",
      import_job: { id: "import-job-1", meeting_id: "imported-meeting", status: "pending", stage: "reading", progress: 0 },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();
    const file = new File(["audio"], "meeting.m4a", { type: "audio/mp4" });

    await api.importRecording(file, "支付服务复盘");

    const request = fetchSpy.mock.calls[0][1] as RequestInit;
    const form = request.body as FormData;
    expect(form.get("title")).toBe("支付服务复盘");
    expect(form.get("file")).toBeInstanceOf(File);
  });

  it("deletes a meeting through the durable cleanup endpoint with an explicit all scope", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ deleted: true }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.deleteMeeting("meeting/old");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting%2Fold?scope=all",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("sends a scoped deletion without losing an AbortSignal", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ deleted: true }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();
    const controller = new AbortController();

    await api.deleteMeeting("meeting/1", "transcript", controller.signal);

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting%2F1?scope=transcript",
      expect.objectContaining({ method: "DELETE", signal: controller.signal }),
    );
  });

  it("loads and updates the local data retention policy", async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response({ retention_policy: "manual_only", updated_at_ms: 1_000 }))
      .mockResolvedValueOnce(response({ retention_policy: "90_days", updated_at_ms: 2_000 }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await expect(api.getDataGovernanceSettings()).resolves.toEqual({
      retentionPolicy: "local_until_user_deletes",
      updatedAtMs: 1_000,
    });
    await expect(api.updateDataGovernanceSettings("90_days")).resolves.toEqual({
      retentionPolicy: "90_days",
      updatedAtMs: 2_000,
    });
    expect(fetchSpy).toHaveBeenLastCalledWith(
      "/v2/data-governance/settings",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ retention_policy: "90_days" }),
      }),
    );
  });

  it("rejects an unsupported retention policy from the server", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      response({ retention_policy: "weekly", updated_at_ms: 1_000 }),
    ));

    await expect(new HttpMeetingApi().getDataGovernanceSettings()).rejects.toThrow(
      "retention_policy is unsupported",
    );
  });

  it("parses the current snake-case V2 snapshot contract", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      response({
        meeting_id: "meeting-1",
        last_seq: 3,
        segments: [
          {
            meeting_id: "meeting-1",
            segment_id: "segment-1",
            final_id: "final-1",
            transcript_seq: 1,
            text: "原始文字",
            normalized_text: "修正文字",
            started_at_ms: 100,
            ended_at_ms: 900,
            revision: 2,
            evidence_hash: "hash-1",
            speaker_id: "cluster-a",
            speaker_label: "Speaker 1",
            speaker_confidence: 0.91,
            created_at_ms: 1_000,
            updated_at_ms: 1_200,
          },
        ],
        semantic_paragraphs: [{
          meeting_id: "meeting-1",
          paragraph_id: "paragraph-1",
          revision: 1,
          text: "修正文字",
          start_ms: 100,
          end_ms: 900,
          status: "stable",
          checkpoint_ids: ["segment-1"],
          speaker_id: "cluster-a",
          speaker_label: "Speaker 1",
          speaker_confidence: 0.88,
          created_at_ms: 1_000,
          updated_at_ms: 1_200,
        }],
        suggestions: [],
        decision_candidates: [
          {
            id: "decision-1",
            text: "先灰度 5%",
            status: "candidate",
            confidence: 0.86,
            evidence_segment_ids: ["segment-1"],
            evidence_spans: [{
              segment_id: "segment-1",
              transcript_seq: 1,
              start_ms: 100,
              end_ms: 900,
              quote: "原始文字",
            }],
            updated_at_ms: 1_300,
          },
        ],
        action_items: [{
          id: "action-1",
          text: "确认回滚负责人",
          status: "candidate",
          confidence: 0.78,
          evidence_segment_ids: ["segment-1"],
          evidence_spans: [],
          owner: "张三",
          deadline: "上线前",
          updated_at_ms: 1_400,
        }],
        risks: [{
          id: "risk-1",
          text: "P99 延迟超标",
          status: "confirmed",
          confidence: 0.92,
          evidence_segment_ids: ["segment-1"],
          evidence_spans: [],
          mitigation: "超过阈值立即回滚",
          updated_at_ms: 1_500,
        }],
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    const snapshot = await api.getSnapshot("meeting-1");

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:8767/v2/meetings/meeting-1/snapshot",
      expect.objectContaining({ headers: expect.objectContaining({ Accept: "application/json" }) }),
    );
    expect(snapshot).toMatchObject({ meetingId: "meeting-1", lastSeq: 3 });
    expect(snapshot.segments[0]).toMatchObject({
      normalizedText: "修正文字",
      revision: 2,
      speakerId: "cluster-a",
      speakerLabel: "Speaker 1",
      speakerConfidence: 0.91,
    });
    expect(snapshot.semanticParagraphs?.[0]).toMatchObject({
      speakerId: "cluster-a",
      speakerLabel: "Speaker 1",
      speakerConfidence: 0.88,
    });
    expect(snapshot.decisionCandidates[0]).toMatchObject({
      id: "decision-1",
      text: "先灰度 5%",
      status: "candidate",
      confidence: 0.86,
    });
    expect(snapshot.decisionCandidates[0].evidenceSpans[0]).toEqual({
      segmentId: "segment-1",
      transcriptSeq: 1,
      startMs: 100,
      endMs: 900,
      quote: "原始文字",
    });
    expect(snapshot.actionItems[0]).toMatchObject({ owner: "张三", deadline: "上线前" });
    expect(snapshot.risks[0]).toMatchObject({ mitigation: "超过阈值立即回滚" });
  });

  it("persists a meeting fact status through the canonical entity endpoint", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ status: "confirmed" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    await api.saveFactStatus("meeting/1", "action_item", "action/1", "confirmed");

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:8767/v2/meetings/meeting%2F1/entities/action%2F1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: "confirmed" }),
      }),
    );
  });

  it("updates a meeting title through the V2 meeting contract", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ meeting: { id: "meeting-1", title: "新的会议名称" } }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    await api.updateMeetingTitle("meeting-1", "  新的会议名称  ");

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:8767/v2/meetings/meeting-1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ title: "新的会议名称" }),
      }),
    );
  });

  it("lists and renames durable meeting-scoped speaker labels", async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response({
        meeting_id: "meeting/1",
        speakers: [{
          speaker_id: "cluster/a",
          speaker_label: "Speaker 1",
          ordinal: 1,
          created_at_ms: 1_000,
          updated_at_ms: 1_000,
        }],
      }))
      .mockResolvedValueOnce(response({
        speaker: {
          speaker_id: "cluster/a",
          speaker_label: "张工",
          ordinal: 1,
          created_at_ms: 1_000,
          updated_at_ms: 2_000,
        },
      }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    await expect(api.getSpeakers("meeting/1")).resolves.toEqual([{
      meetingId: "meeting/1",
      speakerId: "cluster/a",
      speakerLabel: "Speaker 1",
      ordinal: 1,
      createdAtMs: 1_000,
      updatedAtMs: 1_000,
    }]);
    await expect(api.renameSpeaker("meeting/1", "cluster/a", "  张工  ")).resolves.toMatchObject({
      meetingId: "meeting/1",
      speakerId: "cluster/a",
      speakerLabel: "张工",
    });

    expect(fetchSpy).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8767/v2/meetings/meeting%2F1/speakers",
      expect.objectContaining({ headers: expect.objectContaining({ Accept: "application/json" }) }),
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8767/v2/meetings/meeting%2F1/speakers/cluster%2Fa",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ speaker_label: "张工" }),
      }),
    );
  });

  it("uses after_seq for incremental event polling", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      response({
        meeting_id: "meeting-1",
        after_seq: 7,
        last_seq: 7,
        events: [],
        has_more: false,
        next_after_seq: 7,
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi();

    await api.getEvents("meeting-1", 7);
    expect(fetchSpy.mock.calls[0][0]).toBe("/v2/meetings/meeting-1/events?after_seq=7");
  });

  it("downloads a V2 meeting export using the server filename", async () => {
    const exportResponse = new Response("# 会议复盘\n", {
      headers: {
        "content-type": "text/markdown",
        "content-disposition": 'attachment; filename="meeting-1.meeting.md"',
      },
    });
    const fetchSpy = vi.fn().mockResolvedValue(exportResponse);
    const objectUrl = "blob:meeting-export";
    const createObjectUrl = vi.fn().mockReturnValue(objectUrl);
    const revokeObjectUrl = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: createObjectUrl,
      revokeObjectURL: revokeObjectUrl,
    });
    const api = new HttpMeetingApi();

    await api.exportMeeting("meeting-1", "markdown");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting-1/export?format=markdown",
      expect.objectContaining({ headers: { Accept: "text/markdown" } }),
    );
    expect(createObjectUrl).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith(objectUrl);
  });

  it("downloads the allowlist-only runtime diagnostic bundle", async () => {
    const bundleResponse = new Response(new Blob(["diagnostic-zip"]), {
      headers: {
        "content-type": "application/zip",
        "content-disposition": 'attachment; filename="meeting-copilot-diagnostics.zip"',
      },
    });
    const fetchSpy = vi.fn().mockResolvedValue(bundleResponse);
    const objectUrl = "blob:diagnostic-bundle";
    const createObjectUrl = vi.fn().mockReturnValue(objectUrl);
    const revokeObjectUrl = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: createObjectUrl,
      revokeObjectURL: revokeObjectUrl,
    });
    const api = new HttpMeetingApi();

    await api.exportDiagnosticBundle();

    expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/diagnostics/bundle",
      expect.objectContaining({ headers: { Accept: "application/zip" } }),
    );
    expect(createObjectUrl).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith(objectUrl);
  });

  it("rejects malformed fact arrays instead of inventing display data", () => {
    expect(() => parseMeetingSnapshot({ meeting_id: "meeting-1", last_seq: 0, segments: null, suggestions: [] }))
      .toThrow(ContractError);
  });

  it("parses nested durable AI and user document versions", () => {
    const document = parseReviewDocument({
      document_id: "doc-1",
      meeting_id: "meeting-1",
      document_kind: "minutes",
      source_transcript_revision: 4,
      revision: 3,
      ai_generated: { version: 1, content: { markdown: "# AI 初稿" } },
      user_final: { version: 2, content: { markdown: "# 用户最终稿" }, modified: true },
      dirty_state: "saved",
      updated_at_ms: 9_000,
    });
    const revisions = parseReviewDocumentRevisions({
      revisions: [{
        revision: 3,
        version_kind: "user_final",
        author: "user",
        content: { markdown: "# 用户最终稿" },
        created_at_ms: 9_000,
      }],
    });

    expect(document).toMatchObject({
      sourceRevision: 4,
      contentJson: { markdown: "# 用户最终稿" },
      aiVersion: 1,
      userVersion: 2,
      source: "user_final",
    });
    expect(revisions[0]).toMatchObject({ source: "user_final", contentJson: { markdown: "# 用户最终稿" } });
  });

  it("posts a typed ui-rendered trace receipt", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({ trace_id: "job-1" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    await api.markUiRendered("job/1", 9.8, 3.2);

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:8767/v2/traces/job%2F1/ui-rendered",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ event_seq: 9, draft_seq: 3 }),
      }),
    );
  });

  it("uses server-side meeting search, status, and keyset pagination", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(response({
      meetings: [],
      has_more: true,
      next_cursor: { before_updated_at_ms: 8_000, before_meeting_id: "meeting-8" },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new HttpMeetingApi("http://localhost:8767/");

    const page = await api.listMeetingsPage({
      query: "支付 发布",
      status: "processing",
      limit: 24,
      cursor: { beforeUpdatedAtMs: 9_000, beforeMeetingId: "meeting-9" },
    });

    const requestQuery = new URLSearchParams(String(fetchSpy.mock.calls[0][0]).split("?")[1]);
    expect(requestQuery.get("query")).toBe("支付 发布");
    expect(requestQuery.get("status")).toBe("processing");
    expect(requestQuery.get("limit")).toBe("24");
    expect(requestQuery.get("before_updated_at_ms")).toBe("9000");
    expect(requestQuery.get("before_meeting_id")).toBe("meeting-9");
    expect(page).toMatchObject({
      hasMore: true,
      nextCursor: { beforeUpdatedAtMs: 8_000, beforeMeetingId: "meeting-8" },
    });
  });
});
