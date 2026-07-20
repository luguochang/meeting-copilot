import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProductNavigation } from "./ProductNavigation";

describe("ProductNavigation", () => {
  it("connects the live meeting view back to the real meeting list action", async () => {
    const user = userEvent.setup();
    const onOpenMeetings = vi.fn();

    render(<ProductNavigation active="live" onOpenMeetings={onOpenMeetings} />);

    expect(screen.getByRole("button", { name: "会议记录" })).toBeVisible();
    expect(screen.getByText("当前会议，当前页面")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "会议记录" }));
    expect(onOpenMeetings).toHaveBeenCalledOnce();
  });

  it("does not expose planned modules as fake navigation", () => {
    render(<ProductNavigation active="meetings" />);

    expect(screen.queryByText(/知识库|模板|集成|帮助/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "当前会议" })).not.toBeInTheDocument();
    expect(screen.getByText("会议记录，当前页面")).toBeInTheDocument();
  });
});
