import { API_URL } from "@/lib/constants";
import type { ConnectionCreate, ConnectionRead, QueryHistoryRead, SessionRead, SchemaResponse } from "@/types/api";

async function readJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!text.trim()) {
    throw new Error("Server returned an empty response.");
  }

  try {
    return JSON.parse(text) as T;
  } catch (error) {
    console.error("INVALID JSON RESPONSE:", text);
    throw new Error("Invalid response from server.");
  }
}

async function apiFetch<T>(path: string, token?: string | null, init: RequestInit = {}): Promise<T> {
  try {
    const url = `${API_URL}${path}`;
    console.log("CALLING:", url);
    const headers = new Headers(init.headers);

    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    const shouldSendJsonContentType = init.body !== undefined && !(init.body instanceof FormData) && !headers.has("Content-Type");
    if (shouldSendJsonContentType) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(url, {
      ...init,
      headers,
      cache: "no-store",
    });

    console.log("STATUS:", response.status);

    if (!response.ok) {
      const text = await response.text();
      console.error("ERROR RESPONSE:", text);
      try {
        const parsed = JSON.parse(text);
        const message =
          typeof parsed?.detail === "string"
            ? parsed.detail
            : typeof parsed?.message === "string"
              ? parsed.message
              : text;
        throw new Error(message || `Request failed: ${response.status}`);
      } catch {
        throw new Error(text || `Request failed: ${response.status}`);
      }
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return await readJsonResponse<T>(response);
  } catch (err) {
    console.error("FETCH FAILED:", err);
    throw err;
  }
}

export async function createConnection(payload: ConnectionCreate, token?: string | null) {
  return apiFetch<ConnectionRead>("/api/v1/connections", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function testConnection(connectionId: string, token?: string | null) {
  return apiFetch<{ success: boolean; message: string }>(`/api/v1/connections/${connectionId}/test`, token, {
    method: "POST",
  });
}

export async function deleteConnection(connectionId: string, token?: string | null) {
  return apiFetch<void>(`/api/v1/connections/${connectionId}`, token, {
    method: "DELETE",
  });
}

export async function getSchema(connectionId: string, token?: string | null) {
  return apiFetch<SchemaResponse>(`/api/v1/schema/${connectionId}`, token);
}

export async function listConnections(token?: string | null) {
  return apiFetch<ConnectionRead[]>("/api/v1/connections", token);
}

export async function listSessions(token?: string | null) {
  return apiFetch<SessionRead[]>("/api/v1/sessions", token);
}

export async function getSession(sessionId: string, token?: string | null) {
  return apiFetch<{ session: SessionRead; history: QueryHistoryRead[] }>(`/api/v1/sessions/${sessionId}`, token);
}

// --- Documents (RAG) ---

export interface DocumentUploadResult {
  source: string;
  num_chunks: number;
  status: string;
  document_type?: string;
  document_size_bytes?: number;
}

export interface DocumentSearchResult {
  chunk_text: string;
  source: string;
  chunk_index: number;
  score: number;
  metadata: Record<string, unknown>;
}

export async function uploadDocument(file: File, token?: string | null) {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<DocumentUploadResult>("/api/v1/documents/upload", token, {
    method: "POST",
    body: formData,
  });
}

export async function searchDocuments(query: string, token?: string | null, limit = 5) {
  return apiFetch<{ query: string; results: DocumentSearchResult[]; count: number }>(
    `/api/v1/documents/search?query=${encodeURIComponent(query)}&limit=${limit}`,
    token,
    { method: "POST" }
  );
}

// --- Specialists ---

export interface SpecialistInfo {
  id: string;
  name: string;
  description: string;
  capabilities: string[];
  supported_data_types: string[];
  tools: string[];
  available: boolean;
}

export async function listSpecialists(token?: string | null) {
  return apiFetch<{ specialists: SpecialistInfo[]; count: number }>("/api/v1/specialists/", token);
}

// --- Evaluation ---

export interface AuditFinding {
  severity: string;
  category: string;
  message: string;
  details: string;
}

export interface SecurityAuditResult {
  passed: boolean;
  findings: AuditFinding[];
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
}

export async function runSecurityAudit(token?: string | null) {
  return apiFetch<SecurityAuditResult>("/api/v1/evaluation/security-audit", token);
}

// --- Datasets ---

export interface DatasetRead {
  id: string;
  name: string;
  filename: string;
  source_type: string;
  row_count: number | null;
  column_count: number | null;
  created_at: string;
  descriptor: Record<string, unknown> | null;
}

export interface DatasetListResponse {
  datasets: DatasetRead[];
  total: number;
}

export interface TablePreviewResponse {
  dataset_id: string;
  table_name: string;
  columns: string[];
  rows_preview: Record<string, unknown>[];
}

export async function listDatasets(token?: string | null) {
  return apiFetch<DatasetListResponse>("/api/v1/datasets", token);
}

export async function uploadDataset(file: File, token?: string | null) {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<DatasetRead>("/api/v1/datasets", token, {
    method: "POST",
    body: formData,
  });
}

export async function getDataset(datasetId: string, token?: string | null) {
  return apiFetch<DatasetRead>(`/api/v1/datasets/${datasetId}`, token);
}

export async function profileDataset(datasetId: string, token?: string | null) {
  return apiFetch<DatasetRead>(`/api/v1/datasets/${datasetId}/profile`, token, {
    method: "POST",
  });
}

export async function previewDataset(datasetId: string, token?: string | null, limit = 20) {
  return apiFetch<TablePreviewResponse>(
    `/api/v1/datasets/${datasetId}/preview?limit=${limit}`,
    token
  );
}

export async function deleteDataset(datasetId: string, token?: string | null) {
  return apiFetch<void>(`/api/v1/datasets/${datasetId}`, token, {
    method: "DELETE",
  });
}

// --- Simulation ---

export interface SimulationRequest {
  question: string;
  parameters: Record<string, string | number | boolean>;
}

// --- Specialists ---

export async function invokeSpecialist(
  specialistId: string,
  skill: string,
  params: Record<string, unknown>,
  token?: string | null
) {
  return apiFetch<{ specialist_id: string; skill: string; result: unknown }>(
    `/api/v1/specialists/${specialistId}/invoke`,
    token,
    {
      method: "POST",
      body: JSON.stringify({ skill, params }),
    }
  );
}

// --- Evaluation Benchmarks ---

export interface BenchmarkResult {
  benchmark_id: string;
  total_cases: number;
  passed: number;
  failed: number;
  accuracy: number;
  total_time_ms: number;
  cases: Record<string, unknown>[];
}

export async function runNLQBenchmark(token?: string | null) {
  return apiFetch<BenchmarkResult>("/api/v1/evaluation/benchmarks/nlq/run", token, {
    method: "POST",
  });
}

export async function runEDABenchmark(token?: string | null) {
  return apiFetch<BenchmarkResult>("/api/v1/evaluation/benchmarks/eda/run", token, {
    method: "POST",
  });
}

export async function runNLPBenchmark(token?: string | null) {
  return apiFetch<BenchmarkResult>("/api/v1/evaluation/benchmarks/nlp/run", token, {
    method: "POST",
  });
}
