import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProductNavigation } from "./ProductNavigation";

describe("ProductNavigation", () => {
  beforeEach(() => window.localStorage.clear());

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

  it("lets the user switch the sidebar theme and collapsed state", async () => {
    const user = userEvent.setup();
    const { container } = render(<ProductNavigation active="meetings" />);
    const navigation = container.querySelector(".product-navigation");

    expect(navigation).toHaveAttribute("data-theme", "light");
    expect(navigation).toHaveAttribute("data-collapsed", "false");

    await user.click(screen.getByRole("button", { name: "切换为深色侧栏" }));
    await user.click(screen.getByRole("button", { name: "收起侧栏为图标" }));

    expect(navigation).toHaveAttribute("data-theme", "dark");
    expect(navigation).toHaveAttribute("data-collapsed", "true");
    expect(window.localStorage.getItem("meeting-copilot-navigation-theme")).toBe("dark");
    expect(window.localStorage.getItem("meeting-copilot-navigation-collapsed")).toBe("true");
  });

  it("opens local capability management from the shared navigation", async () => {
    const user = userEvent.setup();
    const onOpenCapabilities = vi.fn();
    render(
      <ProductNavigation active="meetings" onOpenCapabilities={onOpenCapabilities} />,
    );

    await user.click(screen.getByRole("button", { name: "离线能力" }));

    expect(onOpenCapabilities).toHaveBeenCalledOnce();
  });
});
