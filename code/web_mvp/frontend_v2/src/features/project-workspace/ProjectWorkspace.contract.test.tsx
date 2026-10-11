import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpProjectOutcomesApi } from "../../api/projectOutcomes";
import { ProjectWorkspace } from "./ProjectWorkspace";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const frozen = {
  source_type: "document", source_id: "span-frozen", document_id: "document-final", project_id: "project-fixture",
  title: "模拟人工终稿", version_id: "version-1", version: 1, content_hash: "frozen-hash", context_revision: 3,
  quote: "尚待核对重复处理边界", locator: { type: "review_document", paragraph: 4, offset: 0, source_id: "review-final", source_version: 1, source_hash: "source-hash" },
};
const proposal = {
  id: "proposal-fixture", project_id: "project-fixture", meeting_id: "meeting-fixture", origin: "ai", base_revision: 4,
  status: "pending", revision: 1, is_stale: false,
  changes: [{ action: "create", type: "question", text: "确认订单重复回调边界", source_refs: [frozen] }],
};
const confirmedItem = {
  id: "question-fixture", project_id: "project-fixture", type: "question", text: "确认订单重复回调边界", status: "unknown",
  owner: null, due: null, source_meeting_id: "meeting-fixture", source_refs: [frozen], origin: "confirmed_proposal", revision: 1, supersedes_id: null,
};

afterEach(() => { vi.unstubAllGlobals(); });

describe("ProjectWorkspace actual-shaped document evidence contract", () => {
  it("loads a frozen candidate without ordinal, reads the exact source and updates Wiki only after explicit guarded confirmation", async () => {
    let confirmed = false;
    const fetcher = vi.fn().mockImplementation(async (input: string, init: RequestInit) => {
      const path = new URL(input).pathname;
      if (path.endsWith("/state")) return response({ items: confirmed ? [confirmedItem] : [], has_more: false, next_cursor: null, project_revision: confirmed ? 5 : 4 });
      if (path.endsWith("/proposals")) return response({ items: [{ ...proposal, status: confirmed ? "confirmed" : "pending", revision: confirmed ? 2 : 1, is_stale: confirmed }], has_more: false, next_cursor: null });
      if (path.endsWith("/wiki")) return response({ project_id: "project-fixture", project_revision: confirmed ? 5 : 4, markdown: confirmed ? "## 开放问题\n- 已确认需要核对订单重复回调边界" : "## 开放问题\n暂无。", items: confirmed ? [confirmedItem] : [], truncated: false });
      if (path.endsWith("/outcome-sources/span-frozen")) return response({ source: { ...frozen, ordinal: 3 } });
      if (path.endsWith("/proposals/proposal-fixture/confirm")) {
        expect(init.method).toBe("POST");
        expect(JSON.parse(String(init.body))).toEqual({ expected_revision: 1, expected_project_revision: 4 });
        confirmed = true;
        return response({ proposal: { ...proposal, status: "confirmed", revision: 2, is_stale: true }, items: [confirmedItem], project_revision: 5 });
      }
      throw new Error(`Unexpected endpoint: ${path}`);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    const api = new HttpProjectOutcomesApi("http://localhost:8768");
    render(<ProjectWorkspace api={api} projectId="project-fixture" initialView="proposals" />);
    expect(await screen.findByText("待你审核")).toBeVisible();
    expect(screen.getByText("确认订单重复回调边界")).toBeVisible();
    expect(screen.queryByText("项目版本字段无效")).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => init.method === undefined)).toBe(true);
    await user.click(screen.getByRole("button", { name: /查看来源 1/ }));
    expect(await screen.findByText(frozen.quote)).toBeVisible();
    const sourceRead = fetcher.mock.calls.find(([input]) => new URL(input).pathname.endsWith("/outcome-sources/span-frozen"));
    expect(new URL(String(sourceRead?.[0])).searchParams.get("project_id")).toBe("project-fixture");
    expect(new URL(String(sourceRead?.[0])).searchParams.get("source_type")).toBe("document");
    await user.click(screen.getByRole("button", { name: "项目 Wiki" }));
    expect(screen.getByText("暂无。")).toBeVisible();
    expect(screen.queryByText("确认订单重复回调边界")).toBeNull();
    expect(fetcher.mock.calls.some(([, init]) => init.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "待审核候选" }));
    await user.click(screen.getByRole("button", { name: "确认这些变更" }));
    await screen.findByText("候选已确认，项目状态与 Wiki 已更新。");
    await waitFor(() => expect(fetcher.mock.calls.filter(([, init]) => init.method === "POST")).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "项目 Wiki" }));
    expect(await screen.findByText("已确认需要核对订单重复回调边界")).toBeVisible();
    expect(screen.queryByText(/请拒绝旧候选/)).toBeNull();
  });

  it("reports a failed initial contract load without falsely showing an empty candidate list, and retries by GET", async () => {
    let available = false;
    const fetcher = vi.fn().mockImplementation(async (input: string) => {
      const path = new URL(input).pathname;
      if (path.endsWith("/state")) return response({ items: [], has_more: false, next_cursor: null, project_revision: 4 });
      if (path.endsWith("/wiki")) return response({ project_id: "project-fixture", project_revision: 4, markdown: "暂无已确认事项", items: [], truncated: false });
      if (path.endsWith("/proposals")) return available ? response({ items: [proposal], has_more: false, next_cursor: null }) : response({ detail: "候选暂不可读，请刷新。" }, 503);
      throw new Error(`Unexpected endpoint: ${path}`);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    render(<ProjectWorkspace api={new HttpProjectOutcomesApi("http://localhost:8768")} projectId="project-fixture" initialView="proposals" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("候选暂不可读，请刷新。");
    expect(screen.getByText("项目内容未读取成功，请刷新项目状态；不能据此判断没有候选。")).toBeVisible();
    expect(screen.queryByText(/暂无候选/)).toBeNull();
    available = true;
    await user.click(screen.getByRole("button", { name: "刷新项目状态" }));
    expect(await screen.findByText("待你审核")).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => init.method === undefined)).toBe(true);
  });
});
