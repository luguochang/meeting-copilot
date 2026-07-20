const LOCAL_BASE_ERROR =
  "本地运行时 API base 必须为空（same-origin）或指向 localhost、127.0.0.1、[::1]";
const LOCAL_CAPTURE_ERROR = "为保护录音隐私，浏览器麦克风只能连接本机会议服务";

export function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase();
  return normalized === "localhost"
    || normalized === "127.0.0.1"
    || normalized === "[::1]"
    || normalized === "::1";
}

function invalidLocalBase(): Error {
  return new Error(LOCAL_BASE_ERROR);
}

function currentPageHref(): string {
  const location = (globalThis as typeof globalThis & {
    location?: { href?: string };
  }).location;
  return location?.href || "http://localhost/";
}

function assertApiPath(path: string): string {
  const normalized = path.trim();
  if (!normalized.startsWith("/") || normalized.startsWith("//")) {
    throw new Error("本地运行时 API 路径必须是绝对路径");
  }
  return normalized;
}

/**
 * Normalize the one runtime base shared by HTTP, SSE, and local ASR.
 * Empty remains empty so fetch callers keep same-origin relative requests.
 */
export function resolveLocalApiBase(
  value: string | null | undefined,
  pageHref = currentPageHref(),
): string {
  const normalized = value?.trim() ?? "";
  if (!normalized) {
    try {
      const page = new URL(pageHref);
      if ((page.protocol === "http:" || page.protocol === "https:")
        && isLoopbackHostname(page.hostname)) {
        return "";
      }
    } catch {
      // Fall through to the same fail-closed product error as an invalid explicit base.
    }
    throw invalidLocalBase();
  }

  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    throw invalidLocalBase();
  }
  if ((parsed.protocol !== "http:" && parsed.protocol !== "https:")
    || !isLoopbackHostname(parsed.hostname)
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash) {
    throw invalidLocalBase();
  }

  const pathname = parsed.pathname.replace(/\/+$/, "");
  return `${parsed.origin}${pathname}`;
}

export function resolveLocalApiUrl(
  path: string,
  baseUrl = "",
  pageHref = currentPageHref(),
): string {
  const normalizedPath = assertApiPath(path);
  const base = resolveLocalApiBase(baseUrl, pageHref);
  if (!base) return new URL(normalizedPath, pageHref).toString();
  return new URL(`${base}${normalizedPath}`).toString();
}

export function resolveLocalWebSocketUrl(
  path: string,
  baseUrl = "",
  pageHref = currentPageHref(),
): string {
  let parsed: URL;
  try {
    parsed = new URL(resolveLocalApiUrl(path, baseUrl, pageHref));
  } catch {
    throw new Error(LOCAL_CAPTURE_ERROR);
  }
  if (!isLoopbackHostname(parsed.hostname)) throw new Error(LOCAL_CAPTURE_ERROR);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(LOCAL_CAPTURE_ERROR);
  }
  parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
  return parsed.toString();
}
