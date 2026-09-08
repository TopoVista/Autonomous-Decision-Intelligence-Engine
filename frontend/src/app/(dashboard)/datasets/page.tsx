"use client";

import { useRef, useState } from "react";
import { BarChart3, Database, Eye, Loader2, Sparkles, Trash2, Upload } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@clerk/nextjs";
import {
  deleteDataset,
  listDatasets,
  previewDataset,
  profileDataset,
  uploadDataset,
  type DatasetRead,
  type TablePreviewResponse,
} from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default function DatasetsPage() {
  const { getToken, isLoaded, userId } = useAuth();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);

  const datasetsQuery = useQuery({
    queryKey: ["datasets"],
    queryFn: async () => listDatasets(await getToken()),
    enabled: isLoaded && Boolean(userId),
    retry: false,
  });

  const previewQuery = useQuery({
    queryKey: ["dataset-preview", previewId],
    queryFn: async () => (previewId ? previewDataset(previewId, await getToken()) : null),
    enabled: Boolean(previewId),
    retry: false,
  });

  const handleUpload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const token = await getToken();
      await uploadDataset(file, token);
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleProfile = async (datasetId: string) => {
    setError(null);
    try {
      const token = await getToken();
      await profileDataset(datasetId, token);
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Profiling failed");
    }
  };

  const handleDelete = async (datasetId: string) => {
    setError(null);
    try {
      const token = await getToken();
      await deleteDataset(datasetId, token);
      if (previewId === datasetId) setPreviewId(null);
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const datasets = datasetsQuery.data?.datasets ?? [];
  const preview: TablePreviewResponse | null = previewQuery.data ?? null;

  return (
    <div className="space-y-6 px-4 py-6 md:px-6 lg:px-8">
      <Card>
        <CardHeader className="border-b border-white/10">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-2xl">
              <Badge className="border-accent/20 bg-accent/10 text-accent">Dataset management</Badge>
              <CardTitle className="mt-4 text-3xl">Upload, profile, and explore datasets</CardTitle>
              <CardDescription className="mt-3 text-base text-fg/72">
                Ingest CSV, TSV, or JSON files. Generate deep profiles and preview rows.
              </CardDescription>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <Metric label="Datasets" value={String(datasets.length)} icon={Database} />
              <Metric label="Format" value="Multi" icon={BarChart3} />
              <Metric label="Profiling" value="Auto" icon={Sparkles} />
            </div>
          </div>
        </CardHeader>
      </Card>
      <UploadSection uploading={uploading} error={error} fileInputRef={fileInputRef} onUpload={handleUpload} />
      <div className="grid gap-6 xl:grid-cols-[1fr,1fr]">
        <DatasetList
          datasets={datasets}
          isLoading={datasetsQuery.isLoading}
          previewId={previewId}
          onPreview={setPreviewId}
          onProfile={handleProfile}
          onDelete={handleDelete}
        />
        <PreviewPanel preview={preview} isLoading={previewQuery.isLoading} />
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="rounded-[22px] border border-white/10 bg-[rgba(10,16,27,0.9)] px-4 py-4">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-accent" />
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-fg">{label}</p>
      </div>
      <p className="mt-3 text-lg font-semibold text-fg">{value}</p>
    </div>
  );
}

function UploadSection({
  uploading,
  error,
  fileInputRef,
  onUpload,
}: {
  uploading: boolean;
  error: string | null;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onUpload: (file: File) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload a dataset</CardTitle>
        <CardDescription>Supported formats: CSV, TSV, and JSON</CardDescription>
      </CardHeader>
      <CardContent>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.tsv,.json"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onUpload(file);
          }}
        />
        <div
          onClick={() => fileInputRef.current?.click()}
          className="flex cursor-pointer flex-col items-center justify-center rounded-[24px] border border-dashed border-white/14 bg-[rgba(10,16,27,0.7)] px-6 py-12 text-center transition hover:border-accent/40 hover:bg-[rgba(10,16,27,0.85)]"
        >
          {uploading ? (
            <Loader2 className="h-8 w-8 animate-spin text-accent" />
          ) : (
            <Upload className="h-8 w-8 text-accent" />
          )}
          <p className="mt-4 text-sm font-medium text-fg">
            {uploading ? "Uploading..." : "Click to upload a dataset"}
          </p>
          <p className="mt-2 text-xs text-muted-fg">CSV, TSV, or JSON files</p>
        </div>
        {error && (
          <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-400">
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DatasetList({
  datasets,
  isLoading,
  previewId,
  onPreview,
  onProfile,
  onDelete,
}: {
  datasets: DatasetRead[];
  isLoading: boolean;
  previewId: string | null;
  onPreview: (id: string | null) => void;
  onProfile: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your datasets</CardTitle>
        <CardDescription>Manage uploaded datasets and trigger profiling</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <p className="text-sm text-muted-fg">Loading datasets...</p>
        ) : datasets.length ? (
          datasets.map((dataset) => (
            <div key={dataset.id} className="rounded-[24px] border border-white/10 bg-[rgba(10,16,27,0.9)] p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-medium text-fg">{dataset.name}</p>
                  <p className="mt-1 text-xs text-muted-fg">{dataset.filename}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge className="border-white/10 bg-white/6 text-fg/80">{dataset.source_type}</Badge>
                    {dataset.row_count != null && (
                      <Badge className="border-white/10 bg-white/6 text-fg/80">{dataset.row_count} rows</Badge>
                    )}
                    {dataset.column_count != null && (
                      <Badge className="border-white/10 bg-white/6 text-fg/80">{dataset.column_count} cols</Badge>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button variant="outline" size="sm" onClick={() => onPreview(previewId === dataset.id ? null : dataset.id)}>
                    <Eye className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => onProfile(dataset.id)}>
                    <Sparkles className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => onDelete(dataset.id)} className="text-red-200 hover:bg-red-500/10 hover:text-red-100">
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          ))
        ) : (
          <div className="rounded-[24px] border border-dashed border-white/12 bg-[rgba(10,16,27,0.7)] px-4 py-10 text-center text-sm text-muted-fg">
            No datasets uploaded yet.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PreviewPanel({ preview, isLoading }: { preview: TablePreviewResponse | null; isLoading: boolean }) {
  const rows = Array.isArray(preview?.rows_preview) ? preview.rows_preview : [];
  if (isLoading) {
    return (
      <Card>
        <CardHeader><CardTitle>Data preview</CardTitle></CardHeader>
        <CardContent><p className="py-10 text-sm text-muted-fg">Loading preview...</p></CardContent>
      </Card>
    );
  }
  if (!preview || !rows.length) {
    return (
      <Card>
        <CardHeader><CardTitle>Data preview</CardTitle></CardHeader>
        <CardContent>
          <div className="rounded-[24px] border border-dashed border-white/12 bg-[rgba(10,16,27,0.7)] px-4 py-10 text-center text-sm text-muted-fg">
            No preview available.
          </div>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Data preview</CardTitle>
        <CardDescription>{preview.table_name} preview, showing {rows.length} row{rows.length === 1 ? "" : "s"}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-hidden rounded-[20px] border border-white/10">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-white/10 text-sm">
              <thead className="bg-white/5 text-left text-[10px] uppercase tracking-[0.2em] text-muted-fg">
                <tr>
                  {preview.columns.map((col) => (
                    <th key={col} className="px-3 py-2.5 font-medium">{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {rows.map((row, idx) => (
                  <tr key={idx} className="hover:bg-white/5">
                    {preview.columns.map((col) => (
                      <td key={col} className="max-w-[12rem] truncate px-3 py-2 text-fg/88">
                        {row[col] === null || row[col] === undefined ? "-" : String(row[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
