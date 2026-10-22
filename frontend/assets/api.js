/** Тонкий клиент backend API. Фронт ходит на тот же origin через nginx-прокси. */

const BASE = "/api/v1";

function headers(extra = {}) {
  const key = localStorage.getItem("transcriber.apiKey") || "";
  return key ? { "X-API-Key": key, ...extra } : extra;
}

async function json(response) {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch { /* тело не JSON — оставляем код статуса */ }
    throw new Error(detail);
  }
  return response.json();
}

export const api = {
  config: () => fetch(`${BASE}/config`, { headers: headers() }).then(json),

  deps: () => fetch(`${BASE}/health/deps`, { headers: headers() }).then(json),

  /** options — JobOptions из docs/06-api.md. onProgress(доля 0..1) — ход загрузки (fetch его не отдаёт, поэтому XHR). */
  createJob(blob, filename, options, onProgress) {
    const form = new FormData();
    form.append("file", blob, filename);
    form.append("options", JSON.stringify(options));
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/jobs`);
      for (const [k, v] of Object.entries(headers())) xhr.setRequestHeader(k, v);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
      xhr.onerror = () => reject(new Error("сеть недоступна"));
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch { /* не JSON */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(body.detail || `HTTP ${xhr.status}`));
      };
      xhr.send(form);
    });
  },

  getJob: (id) => fetch(`${BASE}/jobs/${id}`, { headers: headers() }).then(json),

  exportUrl: (id, fmt) => `${BASE}/jobs/${id}/export?fmt=${fmt}`,

  redeliver: (id) =>
    fetch(`${BASE}/jobs/${id}/redeliver`, { method: "POST", headers: headers() }).then(json),

  /** Админка. В prod за oauth2-proxy: без сессии вместо JSON приходит 401. */
  getPipelineMode: () => fetch(`${BASE}/admin/pipeline-mode`, { headers: headers() }).then(json),

  /** mode: "direct" | "n8n". Действует на задачи, созданные после переключения. */
  setPipelineMode: (mode) =>
    fetch(`${BASE}/admin/pipeline-mode`, {
      method: "PUT",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ mode }),
    }).then(json),
};

/** Опрашивает задачу до финального статуса. */
export async function pollJob(id, onUpdate, { intervalMs = 1500, timeoutMs = 3_600_000 } = {}) {
  const started = Date.now();
  for (;;) {
    const job = await api.getJob(id);
    onUpdate?.(job);
    if (job.status === "done" || job.status === "failed") return job;
    if (Date.now() - started > timeoutMs) throw new Error("превышено время ожидания задачи");
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
