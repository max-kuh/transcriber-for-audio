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

  createJob(blob, filename, options) {
    const form = new FormData();
    form.append("file", blob, filename);
    form.append("options", JSON.stringify(options));
    return fetch(`${BASE}/jobs`, { method: "POST", body: form, headers: headers() }).then(json);
  },

  getJob: (id) => fetch(`${BASE}/jobs/${id}`, { headers: headers() }).then(json),

  exportUrl: (id, fmt) => `${BASE}/jobs/${id}/export?fmt=${fmt}`,

  redeliver: (id) =>
    fetch(`${BASE}/jobs/${id}/redeliver`, { method: "POST", headers: headers() }).then(json),
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
