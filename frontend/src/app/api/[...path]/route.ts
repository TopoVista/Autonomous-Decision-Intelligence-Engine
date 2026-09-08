import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const BACKEND_API_URL = (process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

type RouteContext = {
  params: {
    path: string[];
  };
};

const BLOCKED_REQUEST_HEADERS = new Set([
  "accept-encoding",
  "connection",
  "content-length",
  "expect",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const BLOCKED_RESPONSE_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "transfer-encoding",
]);

function buildUpstreamHeaders(request: NextRequest) {
  const headers = new Headers();

  for (const [key, value] of request.headers.entries()) {
    const normalized = key.toLowerCase();
    if (BLOCKED_REQUEST_HEADERS.has(normalized) || normalized.startsWith("sec-")) {
      continue;
    }
    headers.set(key, value);
  }

  return headers;
}

function buildClientHeaders(response: Response) {
  const headers = new Headers();

  for (const [key, value] of response.headers.entries()) {
    if (BLOCKED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      continue;
    }
    headers.set(key, value);
  }

  headers.set("cache-control", "no-store");
  return headers;
}

async function proxy(request: NextRequest, { params }: RouteContext) {
  if (!process.env.BACKEND_API_URL && process.env.VERCEL) {
    return Response.json({ detail: "The backend service is not configured." }, { status: 503 });
  }
  const upstreamPath = params.path.join("/");
  const targetUrl = `${BACKEND_API_URL}/api/${upstreamPath}${request.nextUrl.search}`;
  const headers = buildUpstreamHeaders(request);

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
    redirect: "manual",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  try {
    const response = await fetch(targetUrl, init);

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: buildClientHeaders(response),
    });
  } catch (error) {
    console.error(`Proxy request failed for ${request.method} ${targetUrl}:`, error);
    return Response.json(
      {
        detail: "Unable to reach the backend service through the frontend proxy.",
      },
      { status: 502 }
    );
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE, proxy as OPTIONS, proxy as HEAD };
