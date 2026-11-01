export interface Order {
  order_id: string;
  customer_id: number | null;
  owner_id: number;
  branch_id: number;
  filename: string;
  total_pages: number;
  bw_pages: number;
  color_pages: number;
  total_price: number | string;
  payment_status: string;
  order_status: string;
  estimated_completion: string | null;
  created_at: string;
}

export interface InventoryItem {
  inventory_id: number;
  branch_id: number;
  item_name: string;
  current_stock: number;
  minimum_stock: number;
  unit_measure: string;
  last_restocked: string;
}

export interface Printer {
  printer_id: number;
  branch_id: number;
  printer_name: string;
  model: string;
  status: string;
  last_seen: string;
}

export interface Analysis {
  status: string;
  message?: string;
  total_pages: number;
  bw_pages: number;
  color_pages: number;
  analyzed_price: number;
  forced_bw_price: number;
  documents: AnalyzedDocument[];
  recommendations: PrintRecommendation[];
}

export interface AnalyzedDocument {
  filename: string;
  sha256: string;
  total_pages: number;
  bw_pages: number;
  color_pages: number;
  blank_pages: number[];
  duplicate: boolean;
  duplicate_of: string | null;
  category: string;
  category_method: string;
  paper_size: string;
  orientation: string;
  potential_cutoff_pages: number[];
  low_resolution_images: Array<{ page_number: number; effective_dpi: number; message: string }>;
  estimated_average_page_coverage_percent: number;
  coverage_note: string;
}

export interface PrintRecommendation {
  filename: string;
  type: string;
  title: string;
  detail: string;
  paper_size?: string;
  orientation?: string;
  color_mode?: string;
  paper_sheets_saved?: number;
  price_savings?: number | null;
  reason?: string;
}

export interface PrintingProfile {
  profile_id: number;
  owner_id?: number;
  profile_name: string;
  printer_id: number | null;
  paper_size: string;
  orientation: string;
  color_mode: string;
  duplex: boolean;
  quality: string;
  copies: number;
}

export interface ReprintSearchItem {
  order_id: string;
  owner_id: number;
  filename: string;
  total_pages: number;
  bw_pages: number;
  color_pages: number;
  total_price: number | string;
  order_status: string;
  created_at: string;
  customer_name: string | null;
  customer_phone: string | null;
}

export interface OrderDocument {
  filename: string;
  url: string;
  total_pages: number | null;
  bw_pages: number | null;
  color_pages: number | null;
}

export interface OrderPrintSettings {
  order_id: string;
  profile_id: number | null;
  printer_id: number | null;
  paper_size: string;
  orientation: string;
  color_mode: string;
  duplex: boolean;
  quality: string;
  copies: number;
  owner_confirmed: boolean;
}

export interface ApiResult<T> {
  status: string;
  message?: string;
  data?: T;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error(`Request failed (${response.status}): the server returned an unexpected response.`);
  }
  const result = (await response.json()) as T & { status?: string; message?: string; detail?: string };
  if (!response.ok || result.status === "error") {
    throw new Error(result.message || result.detail || `Request failed (${response.status})`);
  }
  return result;
}

export function postFormWithProgress<T>(
  path: string,
  data: FormData,
  onProgress: (progress: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    request.addEventListener("load", () => {
      let result: T & { status?: string; message?: string; detail?: string };
      try {
        result = JSON.parse(request.responseText) as T & { status?: string; message?: string };
      } catch {
        reject(new Error("The server returned an invalid response."));
        return;
      }
      if (request.status < 200 || request.status >= 300 || result.status === "error") {
        reject(new Error(result.message || result.detail || `Upload failed (${request.status})`));
        return;
      }
      resolve(result);
    });
    request.addEventListener("error", () => reject(new Error("Could not connect to the server.")));
    request.addEventListener("abort", () => reject(new Error("Upload was cancelled.")));
    request.send(data);
  });
}

export interface PresignedUpload {
  upload_token: string;
  files: Array<{ filename: string; size: number; url: string }>;
}

export async function uploadFilesToStorage(
  files: File[],
  onProgress: (progress: number) => void,
): Promise<string | null> {
  let intent: PresignedUpload;
  try {
    intent = await api<PresignedUpload>("/upload/presign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files: files.map((file) => ({ filename: file.name, size: file.size })),
      }),
    });
  } catch (error) {
    if (error instanceof Error && error.message === "Direct uploads are not enabled.") return null;
    throw error;
  }
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  const sentBytes = files.map(() => 0);

  await Promise.all(intent.files.map((upload, index) => new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", upload.url);
    request.setRequestHeader("Content-Type", "application/pdf");
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      sentBytes[index] = event.loaded;
      onProgress(Math.round((sentBytes.reduce((sum, value) => sum + value, 0) / totalBytes) * 100));
    });
    request.addEventListener("load", () => {
      if (request.status < 200 || request.status >= 300) {
        reject(new Error(`Document upload failed (${request.status}).`));
        return;
      }
      sentBytes[index] = files[index].size;
      onProgress(Math.round((sentBytes.reduce((sum, value) => sum + value, 0) / totalBytes) * 100));
      resolve();
    });
    request.addEventListener("error", () => reject(new Error("Could not connect to document storage.")));
    request.addEventListener("abort", () => reject(new Error("Document upload was cancelled.")));
    request.send(files[index]);
  })));
  return intent.upload_token;
}

export function money(value: number | string): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);
}

export function formatDate(value: string | null): string {
  if (!value) return "Not set";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
